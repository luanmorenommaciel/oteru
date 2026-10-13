"""Acceptance tests for the five attention features using synthetic records."""

from __future__ import annotations

from oteru_attention.core import Snapshot, classify_question

NOW = 1_700_000_000_000


def observation(event: str, *, ts_ms: int = NOW - 1_000, **changes) -> dict:
    row = {
        "ts_ms": ts_ms,
        "source": "log",
        "service": "agent-cli",
        "session_id": "session-a",
        "agent_id": "agent-a",
        "event": event,
        "provenance": "observed",
    }
    row.update(changes)
    return row


def test_prioritized_queue_deduplicates_calls_and_exposes_origin() -> None:
    rows = [
        observation("tool_result", tool_use_id="call-1", tool_name="search", success="false"),
        observation("tool_result", ts_ms=NOW - 2_000, tool_use_id="call-1", success="false"),
        observation("tool_decision", tool_use_id="call-2", decision="reject"),
        observation("permission_mode_changed", from_mode="warn", to_mode="enforce"),
        observation(
            "tools/call",
            source="trace",
            status_code="Error",
            trace_id="f" * 32,
            span_id="a" * 16,
        ),
    ]
    snapshot = Snapshot(rows, now_ms=NOW, hours=1)
    queue = snapshot.queue()

    assert [item["kind"] for item in queue] == [
        "tool_failure",
        "trace_error",
        "tool_rejection",
        "permission_change",
    ]
    assert [item["score"] for item in queue] == [90, 90, 75, 60]
    for item in queue:
        assert snapshot.evidence(item["evidence"]["id"]) == item["evidence"]
        assert item["evidence"]["source_table"].startswith("otel.otel_")
    assert queue[1]["evidence"]["trace_id"] == "f" * 32


def test_supervision_reports_observation_recency_without_claiming_active_state() -> None:
    rows = [
        observation("api_request"),
        observation("api_request", service="other-agent", agent_id="", session_id="s-b"),
        observation(
            "tool_result",
            service="sample-agent",
            agent_id="",
            session_id="sample-session",
            provenance="sample",
            ts_ms=NOW + 60_000,
        ),
    ]
    snapshot = Snapshot(rows, now_ms=NOW, hours=1, demo=True)
    sessions = snapshot.sessions()

    assert len(sessions) == 3
    assert {item["service"] for item in sessions} == {
        "agent-cli",
        "other-agent",
        "sample-agent",
    }
    assert (
        next(item for item in sessions if item["service"] == "other-agent")["identity_basis"]
        == "service.name"
    )
    assert (
        next(item for item in sessions if item["service"] == "sample-agent")["activity"]
        == "not_recent_or_sample"
    )
    assert snapshot.coverage()["future_sample_records"] == 1


def test_supported_questions_have_bounded_answers_and_evidence() -> None:
    rows = [
        observation("tool_result", success="false", tool_use_id="x"),
        observation("api_request", cost_usd="0.020000"),
        observation("api_request", cost_usd="0.030000"),
    ]
    snapshot = Snapshot(rows, now_ms=NOW, hours=1)

    assert classify_question("Quais ferramentas falharam?") == "failures"
    assert classify_question("Quanto custaram as requisições?") == "cost"
    assert classify_question("Onde devo olhar primeiro?") == "priority"
    assert classify_question("Escreva SQL para apagar a tabela") is None
    failures = snapshot.answer("failures")
    assert failures["metric"] == 1
    assert failures["evidence"][0]["tool_use_id"] == "x"
    assert failures["coverage"]["logs"] == 3
    assert failures["rule_version"] == "attention-v1"
    cost = snapshot.answer("cost")
    assert cost["metric"] == "0.050000"
    assert len(cost["evidence"]) == 2
    assert "USD" in cost["text"]


def test_missing_telemetry_and_truncation_are_explicit() -> None:
    empty = Snapshot([], now_ms=NOW, hours=1)
    assert empty.answer("activity")["status"] == "insufficient"
    partial = Snapshot([observation("api_request")], now_ms=NOW, hours=1, truncated=True)
    assert partial.answer("priority")["status"] == "partial"
    assert "incompleto" in partial.answer("priority")["text"]


def test_codex_event_names_and_trace_ids_do_not_invent_sessions() -> None:
    rows = [
        observation(
            "codex.tool_result",
            success="false",
            tool_use_id="call-1",
            session_id="conversation-a",
            trace_id="a" * 32,
        ),
        observation(
            "codex.tool_result",
            success="true",
            session_id="",
            trace_id="b" * 32,
            cost_usd="100.00",
        ),
        observation("codex.api_request", session_id="conversation-a", cost_usd="0.25"),
    ]
    snapshot = Snapshot(rows, now_ms=NOW, hours=1)

    assert [item["kind"] for item in snapshot.queue()] == ["tool_failure"]
    assert len(snapshot.sessions()) == 1
    assert snapshot.sessions()[0]["session_id"] == "conversation-a"
    assert snapshot.answer("cost")["metric"] == "0.250000"
