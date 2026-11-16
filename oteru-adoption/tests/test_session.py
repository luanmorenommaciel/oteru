from datetime import UTC, datetime

from oteru_adoption.session import SessionEvent, build_session_page


def test_session_content_is_hidden_by_default() -> None:
    event = SessionEvent(
        0, "message", datetime.now(UTC), "trace:1", "sha256:abc", 3, 2, 1
    )
    page = build_session_page("session-1", [event])
    assert page["events"][0]["content_digest"] is None
    assert page["tokens"] == {"input": 3, "output": 2, "cache": 1}


def test_partial_session_exposes_sequence_gap() -> None:
    now = datetime.now(UTC)
    events = [SessionEvent(0, "input", now, "a"), SessionEvent(2, "output", now, "b")]
    page = build_session_page("session-1", events)
    assert page["complete"] is False
    assert page["missing_sequences"] == [1]
