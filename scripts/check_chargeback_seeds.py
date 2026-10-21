#!/usr/bin/env python3
"""Chargeback seed guard (#46): no '@' in seeds/chargeback_*; actor keys hmac:v1.

Fails if any file matching ``**/seeds/chargeback_*`` contains ``@`` — including
files under ``seeds/chargeback_fixtures/`` — or if a production CSV in that
glob with an ``actor_id`` column has a data value that does not match
``^hmac:v1:[0-9a-f]{64}$``. Actor keys under ``chargeback_fixtures/`` may be
``hmac:vN:<64 hex>`` (the v2 rotation fixture). Header-only files and an
empty glob pass. Stdlib-only.

The RFC 2606 ``.invalid`` fixture lives only at the exact path
``tests/fixtures/chargeback_guard/at_forbidden.csv`` (documented there and
in ``.gitleaks.toml``). That file does not match ``seeds/chargeback_*``, so
this checker never allowlists it; tests copy it under a matching path to
prove the failure.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SEED_DIR_NAME = "seeds"
SEED_PREFIX = "chargeback_"
FIXTURES_DIR_NAME = "chargeback_fixtures"
ACTOR_ID_COLUMN = "actor_id"
HMAC_RE = re.compile(r"^hmac:v1:[0-9a-f]{64}$")
# dbt fixtures may include hmac:v2: to prove rotation is not silent (#46 3a).
HMAC_FIXTURE_RE = re.compile(r"^hmac:v[0-9]+:[0-9a-f]{64}$")

# Directories that are never production seeds (local venvs, deps, git).
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "site-packages",
    }
)


def _is_chargeback_seed_path(path: Path, root: Path) -> bool:
    """True for ``seeds/chargeback_*`` files and anything under ``seeds/chargeback_*/``."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    seeds_idxs = [i for i, part in enumerate(parts) if part == SEED_DIR_NAME]
    if not seeds_idxs:
        return False
    rest = parts[seeds_idxs[-1] + 1 :]
    return bool(rest) and rest[0].startswith(SEED_PREFIX)


def iter_chargeback_seeds(root: Path) -> list[Path]:
    """Files matching ``**/seeds/chargeback_*`` including ``seeds/chargeback_fixtures/``."""
    root = root.resolve()
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            path = Path(dirpath) / name
            if _is_chargeback_seed_path(path, root):
                found.append(path)
    return sorted(found)


def _csv_rows(text: str) -> tuple[list[str] | None, list[tuple[int, dict[str, str]]]]:
    """Return (fieldnames, [(lineno, row), ...]) for non-comment CSV lines."""
    numbered: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip("\ufeff").lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        numbered.append((lineno, line))
    if not numbered:
        return None, []
    reader = csv.DictReader(io.StringIO("\n".join(line for _, line in numbered)))
    if reader.fieldnames is None:
        return None, []
    fieldnames = [name.strip() if name else name for name in reader.fieldnames]
    rows: list[tuple[int, dict[str, str]]] = []
    # numbered[0] is the header; data rows start at numbered[1].
    for (lineno, _), row in zip(numbered[1:], reader, strict=False):
        rows.append(
            (lineno, {k.strip() if k else k: (v or "").strip() for k, v in row.items()})
        )
    return fieldnames, rows


def check_file(path: Path, rel: str) -> list[str]:
    """Return violation strings for one chargeback seed file."""
    violations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return [f"{rel}: binary/non-utf8 chargeback seed — refuse to scan as text"]

    for lineno, line in enumerate(text.splitlines(), 1):
        if "@" in line:
            violations.append(
                f"{rel}:{lineno}: '@' is forbidden in chargeback seeds (no emails/identifiers in the clear)"
            )

    if path.suffix.lower() != ".csv":
        return violations

    fieldnames, rows = _csv_rows(text)
    looks_like_actor_map = "actor_map" in path.name
    has_actor_id = bool(fieldnames) and ACTOR_ID_COLUMN in fieldnames

    if looks_like_actor_map and rows and not has_actor_id:
        violations.append(
            f"{rel}: chargeback actor map is missing an {ACTOR_ID_COLUMN!r} column"
        )
        return violations

    if not has_actor_id:
        return violations

    hmac_re = HMAC_FIXTURE_RE if FIXTURES_DIR_NAME in path.parts else HMAC_RE
    for lineno, row in rows:
        actor_id = row.get(ACTOR_ID_COLUMN, "")
        if not hmac_re.fullmatch(actor_id):
            violations.append(
                f"{rel}:{lineno}: actor_id {actor_id!r} does not match {hmac_re.pattern}"
            )
    return violations


def check_tree(root: Path) -> list[str]:
    """Scan ``root`` for chargeback seeds; empty glob is OK."""
    violations: list[str] = []
    root = root.resolve()
    for path in iter_chargeback_seeds(root):
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        violations.extend(check_file(path, rel))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--path",
        type=Path,
        default=REPO_ROOT,
        help="tree to scan (default: repository root)",
    )
    args = parser.parse_args(argv)
    root = args.path
    if not root.is_dir():
        print(f"chargeback-seed-guard: not a directory: {root}", file=sys.stderr)
        return 2

    files = iter_chargeback_seeds(root)
    violations = check_tree(root)
    for violation in violations:
        print(violation)
    if violations:
        print(
            f"\nchargeback-seed-guard: {len(violations)} violation(s) in {len(files)} file(s).",
            file=sys.stderr,
        )
        return 1
    print(f"chargeback-seed-guard: OK ({len(files)} file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
