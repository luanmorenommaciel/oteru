"""Local paths and policy loader shared by Collector contract tests."""

import hashlib
import hmac
import importlib.util
import json
import secrets
from pathlib import Path

# Session-local HMAC key. Generated at import; never a committed secret.
HMAC_TEST_KEY = secrets.token_hex(32)


def hmac_v1(field: str, value: str, key: str = HMAC_TEST_KEY) -> str:
    digest = hmac.new(
        key.encode(),
        f"oteru-pii:v1|{field}|{value}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return "hmac:v1:" + digest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("privacy_policy", ROOT / "privacy" / "compile.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def default():
    return json.loads((ROOT / "privacy" / "policy.json").read_text())
