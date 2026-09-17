"""Regression tests for scripts/check_pii.py coverage of generated fixtures.

The trace/log fixtures are built by ``tests/factories.py``, not committed as
JSON — these tests prove the PII guard applies the same rules to those
generated payloads and that a real-looking identity fails the check.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import factories
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_guard():
    spec = importlib.util.spec_from_file_location(
        "check_pii", REPO_ROOT / "scripts" / "check_pii.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def guard():
    return _load_guard()


def _span_with(attrs: list[dict]) -> str:
    """One JSON batch line carrying a span with the given attributes."""
    batch = factories._batch(
        [
            factories.span(
                "claude_code.interaction",
                factories.SPAN_INTERACTION,
                factories.BASE_NS,
                factories.BASE_NS + 1,
                attrs,
            )
        ]
    )
    return json.dumps(batch)


def test_generated_payloads_are_clean(guard):
    payloads = guard._generated_payloads()
    assert payloads, "no builders discovered — guard coverage silently lost"
    for name, lines in payloads:
        for line in lines:
            assert guard._check_line(line) == [], f"{name} payload violates the guard"


def test_real_email_in_generated_payload_is_flagged(guard):
    line = _span_with([factories.s("user.email", "maria.silva@gmail.com")])
    assert any("e-mail" in v for v in guard._check_line(line))


def test_real_identity_in_generated_payload_is_flagged(guard):
    line = _span_with([factories.s("user.id", "8f3a9c21-real-user-uuid")])
    assert any("user.id" in v for v in guard._check_line(line))


def test_real_host_name_in_generated_payload_is_flagged(guard):
    line = _span_with([factories.s("host.name", "macbook-pro-maria.local")])
    assert any("host.name" in v for v in guard._check_line(line))


def test_guard_main_passes_on_repo(guard, capsys):
    assert guard.main() == 0
    assert "generated batch(es)" in capsys.readouterr().out
