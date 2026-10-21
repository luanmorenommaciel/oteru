"""Admin HMAC lookup: fail-closed, collector message format, no leaks."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import io
import logging
import re
import secrets
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "hmac_lookup",
    Path(__file__).resolve().parents[1] / "privacy" / "hmac_lookup.py",
)
lookup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(lookup)


def _run(monkeypatch, key, email, caplog, capsys):
    if key is None:
        monkeypatch.delenv("OTERU_PII_HMAC_KEY", raising=False)
    else:
        monkeypatch.setenv("OTERU_PII_HMAC_KEY", key)
    monkeypatch.setattr(lookup.sys, "stdin", io.StringIO(email))
    with caplog.at_level(logging.DEBUG):
        code = lookup.main()
    out, err = capsys.readouterr()
    return code, out, err, caplog.text


def test_rfc4231_vector_1_matches_collector_digest():
    # Public RFC 4231 test vector 1 — not a repository secret.
    key = bytes.fromhex("0b" * 20)
    assert lookup.digest(key, b"Hi There") == (
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    )


def test_output_matches_collector_message_format_and_regex(monkeypatch, caplog, capsys):
    key = secrets.token_hex(32)
    email = "user@example.com"
    code, out, err, logs = _run(monkeypatch, key, email + "\n", caplog, capsys)
    expected = (
        "hmac:v1:"
        + hmac.new(
            key.encode(),
            f"oteru-pii:v1|user.email|{email}".encode(),
            hashlib.sha256,
        ).hexdigest()
    )
    assert code == 0
    assert re.fullmatch(r"hmac:v1:[0-9a-f]{64}\n", out)
    assert out.strip() == expected
    assert err == ""
    assert email not in out
    assert key not in out
    assert email not in err
    assert key not in err
    assert email not in logs
    assert key not in logs


@pytest.mark.parametrize(
    "key",
    [None, "", "x" * 31],
    ids=["missing", "empty", "31-bytes"],
)
def test_fail_closed_missing_empty_or_short_key(monkeypatch, caplog, capsys, key):
    email = "user@example.com"
    code, out, err, logs = _run(monkeypatch, key, email + "\n", caplog, capsys)
    assert code == 1
    assert out == ""
    assert err == ""
    assert email not in out + err + logs
    if key:
        assert key not in out + err + logs


def test_email_and_key_absent_from_exceptions(monkeypatch, caplog, capsys):
    key = secrets.token_hex(32)
    email = "leaky@example.com"
    try:
        _run(monkeypatch, key, email + "\n", caplog, capsys)
    except Exception as exc:  # noqa: BLE001 — leak check
        text = f"{type(exc).__name__}:{exc}"
        assert email not in text
        assert key not in text
