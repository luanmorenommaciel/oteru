"""Python cross-check of the hmac:v1 format used by the Go hmacconverter.

Recomputes testdata/hmac_v1_crosscheck.json with hmac.new and also checks a
key generated in memory, so Go and Python agree byte for byte.
"""

import hashlib
import hmac
import json
import secrets
from pathlib import Path

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "hmacconverter"
    / "testdata"
    / "hmac_v1_crosscheck.json"
)


def hmac_v1(key: str, field: str, value: str) -> str:
    msg = f"oteru-pii:v1|{field}|{value}".encode()
    return "hmac:v1:" + hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def test_fixture_matches_python_hmac_new():
    fx = json.loads(FIXTURE.read_text())
    assert fx["public_test_pattern"] == "0123456789abcdef", (
        "fixture key must stay the public test-only key"
    )
    key = fx["public_test_pattern"] * fx["public_test_repeat"]
    assert len(key) == 64
    assert fx["cases"]
    for case in fx["cases"]:
        assert case["message"] == f"oteru-pii:v1|{case['field']}|{case['value']}"
        assert hmac_v1(key, case["field"], case["value"]) == case["expected"]


def test_format_with_in_memory_key():
    key = secrets.token_hex(32)
    assert len(key) == 64
    out = hmac_v1(key, "actor_id", "synthetic-actor-0001")
    assert out.startswith("hmac:v1:") and len(out) == len("hmac:v1:") + 64
    assert "synthetic-actor-0001" not in out
    assert out != hmac_v1(secrets.token_hex(32), "actor_id", "synthetic-actor-0001")
