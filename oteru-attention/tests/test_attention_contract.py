"""Queue acceptance tests using synthetic records."""

from __future__ import annotations

from oteru_attention.core import Snapshot

NOW = 1_700_000_000_000


def test_queue_orders_deduplicates_and_keeps_source_event() -> None:
    rows = [
        {
            "ts_ms": NOW - 1000,
            "source": "log",
            "service": "agent-cli",
            "session_id": "session-a",
            "event": "codex.tool_result",
            "tool_use_id": "call-1",
            "success": "false",
        },
        {
            "ts_ms": NOW - 2000,
            "source": "log",
            "service": "agent-cli",
            "session_id": "session-a",
            "event": "codex.tool_result",
            "tool_use_id": "call-1",
            "success": "false",
        },
        {
            "ts_ms": NOW - 1000,
            "source": "log",
            "service": "agent-cli",
            "session_id": "session-a",
            "event": "tool_decision",
            "tool_use_id": "call-2",
            "decision": "rejected",
        },
    ]
    snapshot = Snapshot(rows, now_ms=NOW, hours=1)
    queue = snapshot.queue()
    assert [item["kind"] for item in queue] == ["tool_failure", "tool_rejection"]
    assert [item["score"] for item in queue] == [90, 75]
    assert queue[0]["evidence"]["event"] == "codex.tool_result"
