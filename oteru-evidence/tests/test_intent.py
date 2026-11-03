from datetime import datetime, timedelta, timezone

import pytest
from oteru_evidence.intent import DeclaredIntent, IntentRegistry

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def make_intent(version: int, declared_at: datetime) -> DeclaredIntent:
    return DeclaredIntent(
        intent_id="intent-1",
        version=version,
        spec_ref=f"spec:v{version}",
        author="human:owner",
        declared_at=declared_at,
        execution_started_at=NOW,
        session_id="session-1",
    )


def test_late_intent_is_never_presented_as_available_at_start() -> None:
    assert make_intent(1, NOW - timedelta(seconds=1)).available_at_start is True
    assert make_intent(1, NOW + timedelta(seconds=1)).available_at_start is False


def test_registry_is_append_only_and_contiguous() -> None:
    registry = IntentRegistry()
    registry.add(make_intent(1, NOW))
    registry.add(make_intent(2, NOW + timedelta(seconds=2)))
    with pytest.raises(ValueError, match="contiguous"):
        registry.add(make_intent(4, NOW))
    assert [item.version for item in registry.history("intent-1")] == [1, 2]
