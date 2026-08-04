"""Tests for ``oteru_emitter.model.otlp`` (OTLP/JSON dict -> protobuf)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("opentelemetry.proto")

from factories import SPAN_INTERACTION, TRACE_1  # noqa: E402

from oteru_emitter.model.otlp import normalize_ids, to_request  # noqa: E402


def test_logs_become_export_request(tiny_batches):
    request = to_request("logs", tiny_batches[0].payload)
    assert type(request).__name__ == "ExportLogsServiceRequest"
    records = request.resource_logs[0].scope_logs[0].log_records
    assert records[0].body.string_value == "claude_code.user_prompt"


def test_metrics_become_export_request(tiny_batches):
    request = to_request("metrics", tiny_batches[2].payload)
    assert type(request).__name__ == "ExportMetricsServiceRequest"
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert metric.name == "claude_code.session.count"


def test_unknown_signal():
    with pytest.raises(ValueError, match="unknown signal"):
        to_request("events", {})


def test_traces_become_export_request_with_hex_ids_decoded(traces_batches):
    # OTLP/JSON carries IDs as hex; protobuf JSON wants base64. Without the
    # conversion the collector rejects the batch ("invalid TraceID length").
    request = to_request("traces", traces_batches[0].payload)
    assert type(request).__name__ == "ExportTraceServiceRequest"
    by_name = {s.name: s for s in request.resource_spans[0].scope_spans[0].spans}
    root = by_name["claude_code.interaction"]
    assert root.trace_id.hex() == TRACE_1
    assert root.span_id.hex() == SPAN_INTERACTION
    # children point back at the root, grandchildren at the tool span
    assert by_name["claude_code.llm_request"].parent_span_id.hex() == SPAN_INTERACTION
    assert by_name["claude_code.tool"].parent_span_id.hex() == SPAN_INTERACTION
    tool_span_id = by_name["claude_code.tool"].span_id.hex()
    assert by_name["claude_code.tool.execution"].parent_span_id.hex() == tool_span_id
    assert by_name["claude_code.tool.blocked_on_user"].parent_span_id.hex() == tool_span_id


def test_to_request_does_not_mutate_the_payload(traces_batches):
    payload = traces_batches[0].payload
    before = json.dumps(payload, sort_keys=True)
    to_request("traces", payload)
    assert json.dumps(payload, sort_keys=True) == before


def test_normalize_ids_leaves_non_hex_values_alone():
    # Claude Code's log records carry empty trace IDs — they must pass through
    payload = {"traceId": "", "spanId": "", "other": "5b8aa5a2"}
    assert normalize_ids(payload) == payload


def test_normalize_ids_ignores_wrong_length_ids():
    # not the expected byte length -> left as-is rather than silently mangled
    payload = {"traceId": "abcd", "spanId": "0123456789abcdef01"}
    assert normalize_ids(payload) == payload
