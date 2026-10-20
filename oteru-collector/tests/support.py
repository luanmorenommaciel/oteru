"""Local paths and policy loader shared by Collector contract tests."""

import hashlib
import hmac
import importlib.util
import json
from pathlib import Path

# Public fixture key for unit/e2e expected-hash computation only. Not a secret.
HMAC_TEST_KEY = "oteru-hmac-test-key-32-bytes-min!"


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
