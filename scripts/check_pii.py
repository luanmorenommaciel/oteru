#!/usr/bin/env python3
"""PII guard: scans committed captures for real identity.

The repo's #1 documented risk is committing an un-redacted OTLP capture
(they carry e-mail, account/organization IDs and user paths). This script
is the single verification artifact — reused by the Makefile
(``make pii-guard``), by the pre-commit hook and by CI.

Stdlib-only; runs with the system Python, before any ``make setup``.

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

# ClickHouse exports attributes as a JSON object ({"user.id": "..."}) rather than
# OTLP's array of {key, value} pairs. Without this the identity check silently
# passes on a dump: the keys are there, the regex just never matches them.
MAP_ATTR_RE = re.compile(r'"(?P<key>[A-Za-z_][\w.]*)"\s*:\s*"(?P<value>[^"]*)"')

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

    for rx in (ATTR_RE, MAP_ATTR_RE):
        for match in rx.finditer(line):
            key, value = match.group("key"), match.group("value")
            if key in IDENTITY_KEYS and not _is_placeholder(value):
                violations.append(f"un-redacted identity in {key!r}: {value!r}")
            elif key == "host.name" and not _is_placeholder(value):
                violations.append(f"non-placeholder host.name: {value!r}")

    return violations


def main() -> int:
    # Extra paths may be passed on the command line, so anything about to be
    # shared — a session dump, a generated report — can be checked by the same
    # guard the repo already trusts, instead of by an ad-hoc grep.
    scan_dirs = [Path(arg).resolve() for arg in sys.argv[1:]] or list(SCAN_DIRS)

    files: list[Path] = []
    for scan_dir in scan_dirs:
        if scan_dir.is_dir():
            files.extend(sorted(p for p in scan_dir.rglob("*") if p.is_file()))
        elif scan_dir.is_file():
            files.append(scan_dir)

    if not files:
        print("pii-guard: no files to scan (empty directories?).", file=sys.stderr)
        return 1

    total = 0
    for path in files:
        # A path passed on the command line can live outside the repo.
        rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
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

    if total:
        print(f"\npii-guard: {total} violation(s) — do NOT commit un-redacted captures.")
        return 1

    print(f"pii-guard: OK ({len(files)} clean file(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
