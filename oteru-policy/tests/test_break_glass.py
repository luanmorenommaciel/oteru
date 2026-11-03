from datetime import UTC, datetime, timedelta

import pytest
from oteru_policy.break_glass import BreakGlassOverride, Policy, evaluate


def enforce_policy() -> Policy:
    return Policy("v2", "enforce", "shadow:1", "security:1", "captain")


def test_enforce_requires_gates() -> None:
    with pytest.raises(ValueError, match="requires"):
        Policy("v2", "enforce")


def test_valid_override_is_scoped_and_audited_without_content() -> None:
    now = datetime.now(UTC)
    override = BreakGlassOverride(
        "o1",
        "team/project",
        "provider",
        "incident",
        "INC-1",
        "reviewer",
        now,
        now + timedelta(minutes=10),
    )
    result = evaluate(
        "r1",
        credential_scope="team/project",
        declared_scope="team/project",
        provider="provider",
        policy=enforce_policy(),
        evaluated_at=now,
        override=override,
    )
    assert result["outcome"] == "allow_break_glass"
    assert set(result).isdisjoint({"prompt", "response", "body", "secret"})


def test_expired_override_does_not_bypass_enforcement() -> None:
    now = datetime.now(UTC)
    override = BreakGlassOverride(
        "o1",
        "team/project",
        "provider",
        "incident",
        "INC-1",
        "reviewer",
        now,
        now + timedelta(minutes=1),
    )
    result = evaluate(
        "r1",
        credential_scope="team/project",
        declared_scope=None,
        provider="provider",
        policy=enforce_policy(),
        evaluated_at=now + timedelta(minutes=2),
        override=override,
    )
    assert result["outcome"] == "deny"


def test_unavailable_evaluator_fails_open_with_stale_warning() -> None:
    result = evaluate(
        "r1",
        credential_scope="team/project",
        declared_scope=None,
        provider="provider",
        policy=enforce_policy(),
        evaluated_at=datetime.now(UTC),
        evaluator_available=False,
    )
    assert result["outcome"] == "warn"
    assert result["is_stale"] is True


def test_client_declared_scope_cannot_replace_credential_scope() -> None:
    result = evaluate(
        "r1",
        credential_scope="team/a",
        declared_scope="team/b",
        provider="provider",
        policy=Policy("v1"),
        evaluated_at=datetime.now(UTC),
    )
    assert result["outcome"] == "deny"
