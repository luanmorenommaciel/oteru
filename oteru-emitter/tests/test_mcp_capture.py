"""MCP calls as first-class spans (#42): the ``mcp_capture`` fixture contract.

The fixture is what the MCP view (oteru-collector/clickhouse/views/mcp_calls.sql)
and ``make e2e-signals`` run against, so its shape is pinned here from the
OTel MCP semantic conventions — not from whatever the builder happens to emit:

- every MCP span carries ``mcp.method.name``;
- ``tools/call`` spans name the tool (``gen_ai.tool.name``) and are
  ``execute_tool`` operations, so they count with every other tool call;
- ``resources/read`` spans name the resource (``mcp.resource.uri``);
- a failed call has ERROR status and ``error.type``;
- client and server sides of one call are parent/child in the same trace,
  under different ``service.name``s (agent vs MCP server).
"""

from __future__ import annotations

from collections import Counter

import pytest
from factories import MCP_EXPECTED_METHODS, mcp_capture

from oteru_emitter.rewrite.restamp import restamp
from oteru_emitter.sources.replay import Batch

KIND_CLIENT, KIND_SERVER = 3, 2
STATUS_ERROR = 2


def _spans(batches: list[dict]):
    """(service.name, span) for every span in the capture."""
    for batch in batches:
        for rs in batch["resourceSpans"]:
            service = next(
                a["value"]["stringValue"]
                for a in rs["resource"]["attributes"]
                if a["key"] == "service.name"
            )
            for ss in rs["scopeSpans"]:
                for span in ss["spans"]:
                    yield service, span


def _attrs(span: dict) -> dict:
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


def _mcp_spans(batches):
    return [(svc, s) for svc, s in _spans(batches) if "mcp.method.name" in _attrs(s)]


def test_method_counts_match_the_declared_expectation():
    """MCP_EXPECTED_METHODS is what the e2e asserts against the view."""
    counts = Counter(_attrs(s)["mcp.method.name"] for _, s in _mcp_spans(mcp_capture()))
    assert counts == MCP_EXPECTED_METHODS


def test_covers_tool_calls_and_resource_fetches():
    assert {"tools/call", "resources/read"} <= set(MCP_EXPECTED_METHODS)


def test_tools_call_spans_name_the_tool_and_are_execute_tool():
    calls = [
        s for _, s in _mcp_spans(mcp_capture()) if _attrs(s)["mcp.method.name"] == "tools/call"
    ]
    assert calls
    for span in calls:
        attrs = _attrs(span)
        assert attrs["gen_ai.tool.name"]
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert span["name"] == f"tools/call {attrs['gen_ai.tool.name']}"


def test_resource_reads_name_the_resource():
    reads = [
        s for _, s in _mcp_spans(mcp_capture()) if _attrs(s)["mcp.method.name"] == "resources/read"
    ]
    assert reads and all(_attrs(s)["mcp.resource.uri"] for s in reads)


def test_requests_carry_a_request_id_notifications_do_not_need_one():
    for _, span in _mcp_spans(mcp_capture()):
        attrs = _attrs(span)
        if not attrs["mcp.method.name"].startswith("notifications/"):
            assert "jsonrpc.request.id" in attrs, span["name"]


def test_failed_call_has_error_status_and_error_type():
    failed = [s for _, s in _mcp_spans(mcp_capture()) if s["status"]["code"] == STATUS_ERROR]
    assert failed
    assert all(_attrs(s).get("error.type") for s in failed)


def test_client_and_server_sides_are_linked_across_services():
    spans = list(_spans(mcp_capture()))
    by_id = {s["spanId"]: (svc, s) for svc, s in spans}
    servers = [(svc, s) for svc, s in spans if s["kind"] == KIND_SERVER]
    assert servers
    for svc, server in servers:
        parent_svc, parent = by_id[server["parentSpanId"]]
        assert parent["kind"] == KIND_CLIENT
        assert parent["traceId"] == server["traceId"]
        assert parent_svc != svc
        assert _attrs(parent)["jsonrpc.request.id"] == _attrs(server)["jsonrpc.request.id"]


def test_every_parent_exists_in_the_same_trace():
    spans = [s for _, s in _spans(mcp_capture())]
    ids = {(s["traceId"], s["spanId"]) for s in spans}
    for span in spans:
        if "parentSpanId" in span:
            assert (span["traceId"], span["parentSpanId"]) in ids


@pytest.mark.parametrize("seed", [1, 2])
def test_replay_rotation_keeps_the_cross_service_tree(seed):
    """Client and server spans sit in different resourceSpans; rotation must
    still map the server's parentSpanId to the client's new spanId."""
    batches = [Batch("traces", payload, None) for payload in mcp_capture()]
    restamp(batches, shift_time=False, rotate_keys=(), rotate_trace_ids=True, seed=seed)
    rotated = [b.payload for b in batches]
    spans = [s for _, s in _spans(rotated)]
    ids = {(s["traceId"], s["spanId"]) for s in spans}
    assert all((s["traceId"], s["parentSpanId"]) in ids for s in spans if "parentSpanId" in s)
    original = {s["traceId"] for _, s in _spans(mcp_capture())}
    assert {s["traceId"] for s in spans}.isdisjoint(original)
