#!/usr/bin/env python3
"""PII guard: scans committed captures and generated fixtures for real identity.

The repo's #1 documented risk is committing an un-redacted OTLP capture
(they carry e-mail, account/organization IDs and user paths). This script
is the single verification artifact — reused by the Makefile
(``make pii-guard``), by the pre-commit hook and by CI.

Coverage: every file under ``SCAN_DIRS`` (committed captures/fixtures) plus
the OTLP payloads built programmatically by ``oteru-emitter/tests/factories.py``
(trace/log fixtures no longer live as JSON on disk — the guard builds them
in memory and applies the same rules).

Rules (violation -> ``file:line`` + description, exit 1):
1. E-mails outside the reserved ``example.com/org/net`` domains.
2. User paths: ``C:\\Users\\<name>`` (incl. JSON-escaped),
   ``/home/<name>``, ``/Users/<name>``.
3. Key-aware: ``user.id``, ``user.account_uuid``, ``user.account_id`` and
   ``organization.id`` must hold placeholder values (all-zero hex,
   repeated-digit UUID, ``user_REDACTED...``). Real ``session.id`` /
   ``request_id`` values are allowed: they are correlation, not identity.
4. ``host.name`` with a non-placeholder value, when present.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories holding committed captures/fixtures — the only places a
# capture may legitimately live in the repo.
SCAN_DIRS = (
    REPO_ROOT / "oteru-emitter" / "samples",
    REPO_ROOT / "oteru-emitter" / "tests" / "fixtures",
)

# Trace/log fixtures are built, not committed. Loading factories.py directly
# (no package import) keeps this script stdlib-only and usable before
# ``make setup`` — factories.py itself has no third-party imports.
FACTORIES_PATH = REPO_ROOT / "oteru-emitter" / "tests" / "factories.py"

ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# Raw or JSON-escaped C:\Users\<name> (C:\\Users\\<name>), plus POSIX /home|/Users.
USER_PATH_RES = (
    re.compile(r"[A-Za-z]:[\\/]{1,4}Users[\\/]{1,4}[A-Za-z0-9._-]+"),
    re.compile(r"/home/[A-Za-z0-9._-]+"),
    re.compile(r"/Users/[A-Za-z0-9._-]+"),
)

# Principal-identity keys: the value MUST be a placeholder.
IDENTITY_KEYS = ("user.id", "user.account_uuid", "user.account_id", "organization.id")

ATTR_RE = re.compile(
    r'"key"\s*:\s*"(?P<key>[^"]+)"\s*,\s*"value"\s*:\s*\{\s*"stringValue"\s*:\s*"(?P<value>[^"]*)"'
)

PLACEHOLDER_RES = (
    re.compile(r"^0+$"),  # all-zero hex/digits
    re.compile(r"^(\d)\1{7}-\1{4}-\1{4}-\1{4}-\1{12}$"),  # repeated-digit UUID
    re.compile(r"^user_REDACTED"),
    re.compile(r"REDACTED"),
)


def _is_placeholder(value: str) -> bool:
    return any(rx.search(value) for rx in PLACEHOLDER_RES)


def _check_line(line: str) -> list[str]:
    """Returns the violation descriptions found on the line."""
    violations: list[str] = []

    for match in EMAIL_RE.finditer(line):
        domain = match.group(1).lower()
        if not any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS):
            violations.append(f"e-mail outside example.com/org/net: {match.group(0)!r}")

    for rx in USER_PATH_RES:
        for match in rx.finditer(line):
            violations.append(f"user path: {match.group(0)!r}")

    for match in ATTR_RE.finditer(line):
        key, value = match.group("key"), match.group("value")
        if key in IDENTITY_KEYS and not _is_placeholder(value):
            violations.append(f"un-redacted identity in {key!r}: {value!r}")
        elif key == "host.name" and not _is_placeholder(value):
            violations.append(f"non-placeholder host.name: {match.group(0)!r}")

    return violations


def _generated_payloads() -> list[tuple[str, list[str]]]:
    """Builds every zero-arg ``factories.py`` builder returning a list of
    batches; returns ``(builder_name, json_lines)`` pairs for scanning."""
    spec = importlib.util.spec_from_file_location("factories", FACTORIES_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    payloads: list[tuple[str, list[str]]] = []
    for name in sorted(dir(module)):
        if name.startswith("_"):
            continue
        fn = getattr(module, name)
        if not callable(fn) or inspect.signature(fn).parameters:
            continue
        result = fn()
        if isinstance(result, list) and all(isinstance(b, dict) for b in result):
            payloads.append((name, [json.dumps(b) for b in result]))
    return payloads


def main() -> int:
    files: list[Path] = []
    for scan_dir in SCAN_DIRS:
        if scan_dir.is_dir():
            files.extend(sorted(p for p in scan_dir.rglob("*") if p.is_file()))

    if not files and not FACTORIES_PATH.is_file():
        print("pii-guard: nothing to scan (empty directories?).", file=sys.stderr)
        return 1

    total = 0
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"{rel}: binary file in a captures directory — check it manually.")
            total += 1
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for violation in _check_line(line):
                print(f"{rel}:{lineno}: {violation}")
                total += 1

    generated = 0
    if FACTORIES_PATH.is_file():
        try:
            payloads = _generated_payloads()
        except Exception as exc:  # a broken builder must not mute the guard
            print(f"factories.py: could not build payloads ({exc}) — guard coverage lost.")
            return 1
        for name, lines in payloads:
            generated += len(lines)
            for lineno, line in enumerate(lines, 1):
                for violation in _check_line(line):
                    print(f"factories.py:{name}:{lineno}: {violation}")
                    total += 1

    if total:
        print(f"\npii-guard: {total} violation(s) — do NOT commit un-redacted captures.")
        return 1

    print(f"pii-guard: OK ({len(files)} clean file(s), {generated} generated batch(es)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
