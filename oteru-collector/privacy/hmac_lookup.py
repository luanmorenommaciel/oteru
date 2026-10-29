#!/usr/bin/env python3
"""Admin-only HMAC lookup for chargeback actor_id (#46 criterion 15).

Who may run this: Vinícius and collector administrators only. The HMAC key
never belongs in the repo, CI, argv, or a file written by this command.

Reads the email solely from stdin (never argv) and the key solely from
OTERU_PII_HMAC_KEY. On success prints only ``hmac:v1:<64 lowercase hex>``.
Fails closed (exit 1, no stdout/stderr) if the key is missing, empty, or
shorter than 32 bytes. Does not log.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys

HMAC_KEY_ENV = "OTERU_PII_HMAC_KEY"
MIN_KEY_BYTES = 32
EMAIL_FIELD = "user.email"


def digest(key: bytes, value: bytes) -> str:
    """Same HMAC-SHA256 hex as hmacconverter.Digest (RFC 4231 compatible)."""
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def actor_id(email: str, key: bytes) -> str:
    message = f"oteru-pii:v1|{EMAIL_FIELD}|{email}".encode()
    return "hmac:v1:" + digest(key, message)


def _stdin_email() -> str:
    text = sys.stdin.read()
    if text.endswith("\r\n"):
        return text[:-2]
    if text.endswith("\n"):
        return text[:-1]
    return text


def main(_argv: list[str] | None = None) -> int:
    # Email is never taken from argv (shell history). Extra args are ignored.
    raw = os.environ.get(HMAC_KEY_ENV)
    if raw is None or raw == "" or len(raw.encode()) < MIN_KEY_BYTES:
        return 1
    sys.stdout.write(actor_id(_stdin_email(), raw.encode()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
