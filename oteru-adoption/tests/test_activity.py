from datetime import UTC, datetime, timedelta

import pytest
from oteru_adoption.activity import ActivityEvent, aggregate_activity


def test_activity_is_deduplicated_and_keeps_source_kind() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    event = ActivityEvent("e1", "hmac:v1:a", "agent", "svc", "proc", start, "real")
    rows = aggregate_activity(
        [event, event], window_start=start, window_end=start + timedelta(hours=1)
    )
    assert rows[0]["event_count"] == 1
    assert rows[0]["source_kind"] == "real"


def test_plain_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="HMAC"):
        ActivityEvent(
            "e1", "person@example.com", "a", "s", "p", datetime.now(UTC), "real"
        )
