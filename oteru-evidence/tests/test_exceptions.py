from datetime import datetime, timedelta, timezone

import pytest
from oteru_evidence.exceptions import ExceptionRecord, ExceptionState

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def test_critical_exception_cannot_exist_without_owner() -> None:
    with pytest.raises(ValueError, match="requires an owner"):
        ExceptionRecord("e", "critical", NOW, timedelta(hours=1), "event:1")


def test_transfer_and_resolution_preserve_history() -> None:
    record = ExceptionRecord("e", "high", NOW, timedelta(hours=4), "event:1")
    record.transition(
        ExceptionState.ASSIGNED,
        actor="router",
        occurred_at=NOW,
        reason="initial assignment",
        owner="team:a",
    )
    record.transition(
        ExceptionState.ASSIGNED,
        actor="team:a",
        occurred_at=NOW + timedelta(minutes=5),
        reason="specialist transfer",
        owner="team:b",
    )
    record.transition(
        ExceptionState.RESOLVED,
        actor="team:b",
        occurred_at=NOW + timedelta(minutes=10),
        reason="verified fix",
    )
    assert record.owner == "team:b"
    assert record.state is ExceptionState.RESOLVED
    assert [item.owner for item in record.history] == ["team:a", "team:b", "team:b"]
