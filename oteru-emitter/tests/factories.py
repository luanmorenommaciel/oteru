"""Builders for the synthetic OTLP payloads the tests run against.

Test input is **code, not committed data**: this repo ships what you need to
run it locally and never captured telemetry. Anyone wanting realistic traffic
points the emitter at their own capture.

The trace payload mirrors the span schema Claude Code emits under the traces
beta — span names, attribute keys and hierarchy were read off a real capture
(2026-07-29, Claude Code 2.1.191 and 2.1.220) — but every value here is
fabricated, and identity is placeholder-only so the PII guard stays happy.
"""

from __future__ import annotations

BASE_NS = 1_752_620_000_000_000_000  # fixed anchor; replay restamps it to "now"
MS = 1_000_000

TRACE_1 = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACE_2 = "9d7c1a5e3b28f460c1d4e8a2b6f03957"

SPAN_INTERACTION = "a1b2c3d4e5f60718"
SPAN_LLM_REQUEST = "b2c3d4e5f6071829"
SPAN_TOOL = "c3d4e5f607182930"
SPAN_TOOL_BLOCKED = "d4e5f60718293041"
SPAN_TOOL_EXEC = "e5f6071829304152"
SPAN_INTERACTION_2 = "f60718293041526a"

# The trace scope was renamed between versions: `.traces` up to 2.1.170,
# `.tracing` from 2.1.191 on. Keep this in step with the version above.
SCOPE = {"name": "com.anthropic.claude_code.tracing", "version": "2.1.220"}

# Mirrors the real capture: no identity in the resource block, no host.name.
RESOURCE = {
    "attributes": [
        {"key": "host.arch", "value": {"stringValue": "arm64"}},
        {"key": "os.type", "value": {"stringValue": "darwin"}},
        {"key": "os.version", "value": {"stringValue": "25.5.0"}},
        {"key": "service.name", "value": {"stringValue": "claude-code"}},
        {"key": "service.version", "value": {"stringValue": "2.1.220"}},
    ]
}


def s(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def i(key: str, value: int) -> dict:
    return {"key": key, "value": {"intValue": str(value)}}


def b(key: str, value: bool) -> dict:
    return {"key": key, "value": {"boolValue": value}}


# Identity rides on the spans, as it does on the real log records. All
# placeholders: see scripts/check_pii.py for what counts as one.
IDENTITY = [
    s("user.id", "user_REDACTED_0001"),
    s("session.id", "8c1d3f47-2a9b-4e56-b0c8-7d1e9f3a5b24"),
    s("organization.id", "00000000-0000-0000-0000-000000000000"),
    s("user.email", "user@example.com"),
    s("user.account_uuid", "11111111-1111-1111-1111-111111111111"),
    s("user.account_id", "22222222-2222-2222-2222-222222222222"),
    s("terminal.type", "vscode"),
]


def span(
    name: str,
    span_id: str,
    start_ns: int,
    end_ns: int,
    attributes: list[dict],
    *,
    trace_id: str = TRACE_1,
    parent_span_id: str | None = None,
) -> dict:
    out = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [s("span.type", name), *IDENTITY, *attributes],
        "status": {"code": 1},
    }
    if parent_span_id is not None:
        out["parentSpanId"] = parent_span_id
    return out


def _batch(spans: list[dict]) -> dict:
    return {
        "resourceSpans": [{"resource": RESOURCE, "scopeSpans": [{"scope": SCOPE, "spans": spans}]}]
    }


def traces_capture() -> list[dict]:
    """Two OTLP trace batches: a full interaction tree, then a second one.

    Tree (trace 1): interaction -> llm_request, and interaction -> tool ->
    {blocked_on_user, execution}. Trace 2 is a lone interaction, so tests can
    tell per-trace behaviour from per-batch behaviour.
    """
    interaction = span(
        "claude_code.interaction",
        SPAN_INTERACTION,
        BASE_NS,
        BASE_NS + 8_400 * MS,
        [
            s("user_prompt", "<REDACTED>"),
            i("user_prompt_length", 63),
            i("interaction.sequence", 1),
            i("interaction.duration_ms", 8400),
        ],
    )
    llm_request = span(
        "claude_code.llm_request",
        SPAN_LLM_REQUEST,
        BASE_NS + 120 * MS,
        BASE_NS + 3_950 * MS,
        [
            s("model", "claude-opus-5"),
            s("gen_ai.request.model", "claude-opus-5"),
            s("gen_ai.system", "anthropic"),
            s("gen_ai.response.id", "msg_01REDACTEDrespid0001"),
            s("gen_ai.response.finish_reasons", "tool_use"),
            s("llm_request.context", "interaction"),
            s("speed", "normal"),
            i("duration_ms", 3830),
            i("ttft_ms", 610),
            i("input_tokens", 27126),
            i("output_tokens", 412),
            i("cache_read_tokens", 24880),
            i("cache_creation_tokens", 1240),
            s("request_id", "req_011CbtJR1bZ4uLs3hmdd96uE"),
            s("client_request_id", "3f1c9d2e-5a47-4b80-9c16-8e2d7a4f6b03"),
            i("attempt", 1),
            b("success", True),
            s("stop_reason", "tool_use"),
        ],
        parent_span_id=SPAN_INTERACTION,
    )
    tool = span(
        "claude_code.tool",
        SPAN_TOOL,
        BASE_NS + 4_010 * MS,
        BASE_NS + 5_600 * MS,
        [
            s("tool_name", "Bash"),
            i("duration_ms", 1590),
            s("tool_use_id", "toolu_01A09q90qw90lq917835lq9a"),
            s("gen_ai.tool.call.id", "toolu_01A09q90qw90lq917835lq9a"),
        ],
        parent_span_id=SPAN_INTERACTION,
    )
    blocked = span(
        "claude_code.tool.blocked_on_user",
        SPAN_TOOL_BLOCKED,
        BASE_NS + 4_020 * MS,
        BASE_NS + 4_170 * MS,
        [i("duration_ms", 150), s("decision", "accept"), s("source", "config")],
        parent_span_id=SPAN_TOOL,
    )
    execution = span(
        "claude_code.tool.execution",
        SPAN_TOOL_EXEC,
        BASE_NS + 4_180 * MS,
        BASE_NS + 5_560 * MS,
        [
            i("duration_ms", 1380),
            b("success", True),
            s("tool_use_id", "toolu_01A09q90qw90lq917835lq9a"),
            s("gen_ai.tool.call.id", "toolu_01A09q90qw90lq917835lq9a"),
        ],
        parent_span_id=SPAN_TOOL,
    )
    interaction_2 = span(
        "claude_code.interaction",
        SPAN_INTERACTION_2,
        BASE_NS + 12_000 * MS,
        BASE_NS + 15_200 * MS,
        [
            s("user_prompt", "<REDACTED>"),
            i("user_prompt_length", 28),
            i("interaction.sequence", 2),
            i("interaction.duration_ms", 3200),
        ],
        trace_id=TRACE_2,
    )
    return [
        _batch([interaction, llm_request, tool, blocked, execution]),
        _batch([interaction_2]),
    ]


def logs_sharing_trace_context() -> list[dict]:
    """One logs batch whose records carry trace 1's context.

    Real Claude Code log records carry traceId/spanId once the traces beta is
    on; this is what proves an ID rotation keeps log/trace correlation intact.
    """

    def record(body: str, span_id: str, offset_ms: int) -> dict:
        ts = str(BASE_NS + offset_ms * MS)
        return {
            "timeUnixNano": ts,
            "observedTimeUnixNano": ts,
            "traceId": TRACE_1,
            "spanId": span_id,
            "body": {"stringValue": body},
            "attributes": [*IDENTITY, s("event.name", body)],
        }

    return [
        {
            "resourceLogs": [
                {
                    "resource": RESOURCE,
                    "scopeLogs": [
                        {
                            "scope": {
                                "name": "com.anthropic.claude_code.events",
                                "version": "2.1.220",
                            },
                            "logRecords": [
                                record("claude_code.user_prompt", SPAN_INTERACTION, 10),
                                record("claude_code.api_request", SPAN_LLM_REQUEST, 130),
                            ],
                        }
                    ],
                }
            ]
        }
    ]


# --- MCP (#42) -----------------------------------------------------------------
#
# An agent calling an MCP server, shaped by the OTel MCP semantic conventions
# (open-telemetry/semantic-conventions-genai, model/mcp, read 2026-10-06):
# span name `{mcp.method.name} {target}`, CLIENT on the caller, SERVER on the
# MCP server, `gen_ai.operation.name = execute_tool` on tools/call. Unlike the
# Claude Code trace above, this is not read off a real capture — it is the
# conventions written out, so the MCP view has something conforming to query.

MCP_TRACE_1 = "1c9e7b52d04a4f3e8a6b2d0c5e7f9a13"
MCP_TRACE_2 = "7a3f5c1e9b2d4086a4c8e0f2b6d1a5c9"

MCP_AGENT_RESOURCE = {
    "attributes": [
        {"key": "service.name", "value": {"stringValue": "support-agent"}},
        {"key": "service.version", "value": {"stringValue": "0.1.0"}},
    ]
}
MCP_SERVER_RESOURCE = {
    "attributes": [
        {"key": "service.name", "value": {"stringValue": "orders-mcp-server"}},
        {"key": "service.version", "value": {"stringValue": "0.1.0"}},
    ]
}
MCP_SCOPE = {"name": "oteru.fixture.mcp", "version": "0.1.0"}

# What `mcp_capture()` holds per method — asserted by the fixture test and by
# `make e2e-signals` against the otel.mcp_calls view.
MCP_EXPECTED_METHODS = {
    "tools/call": 3,  # client + server of a success, client of a failure
    "resources/read": 1,
    "prompts/get": 1,
    "notifications/progress": 1,
}


def _mcp_span(
    name: str,
    span_id: str,
    start_ms: int,
    end_ms: int,
    attributes: list[dict],
    *,
    trace_id: str,
    kind: int = 3,  # CLIENT
    parent_span_id: str | None = None,
    status: dict | None = None,
) -> dict:
    out = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(BASE_NS + start_ms * MS),
        "endTimeUnixNano": str(BASE_NS + end_ms * MS),
        "attributes": attributes,
        "status": status or {"code": 1},
    }
    if parent_span_id is not None:
        out["parentSpanId"] = parent_span_id
    return out


def _resource_spans(resource: dict, spans: list[dict]) -> dict:
    return {"resource": resource, "scopeSpans": [{"scope": MCP_SCOPE, "spans": spans}]}


def mcp_capture() -> list[dict]:
    """Two OTLP trace batches of an agent using an MCP server.

    Trace 1: invoke_agent -> chat -> prompts/get -> resources/read ->
    tools/call (client, agent side) -> tools/call (server, MCP-server side),
    plus a notifications/progress. Trace 2: a tools/call that fails with a
    tool error (CallToolResult.isError -> error.type "tool_error").
    """
    t1, t2 = MCP_TRACE_1, MCP_TRACE_2
    session = s("mcp.session.id", "mcp-session-0001")
    proto = s("mcp.protocol.version", "2025-11-25")

    agent = _mcp_span(
        "invoke_agent support-agent",
        "1a2b3c4d5e6f7081",
        0,
        2_400,
        [s("gen_ai.operation.name", "invoke_agent"), s("gen_ai.agent.name", "support-agent")],
        trace_id=t1,
        kind=1,
    )
    chat = _mcp_span(
        "chat claude-sonnet-5-5",
        "2b3c4d5e6f708192",
        20,
        900,
        [
            s("gen_ai.operation.name", "chat"),
            s("gen_ai.provider.name", "anthropic"),
            s("gen_ai.request.model", "claude-sonnet-5-5"),
            i("gen_ai.usage.input_tokens", 1830),
            i("gen_ai.usage.output_tokens", 142),
        ],
        trace_id=t1,
        parent_span_id=agent["spanId"],
    )
    prompt = _mcp_span(
        "prompts/get triage",
        "3c4d5e6f708192a3",
        905,
        930,
        [
            s("mcp.method.name", "prompts/get"),
            s("gen_ai.prompt.name", "triage"),
            s("jsonrpc.request.id", "1"),
            session,
            proto,
        ],
        trace_id=t1,
        parent_span_id=agent["spanId"],
    )
    resource = _mcp_span(
        "resources/read",
        "4d5e6f708192a3b4",
        935,
        990,
        [
            s("mcp.method.name", "resources/read"),
            s("mcp.resource.uri", "orders://policies/refunds"),
            s("jsonrpc.request.id", "2"),
            session,
            proto,
        ],
        trace_id=t1,
        parent_span_id=agent["spanId"],
    )
    tool_attrs = [
        s("mcp.method.name", "tools/call"),
        s("gen_ai.operation.name", "execute_tool"),
        s("gen_ai.tool.name", "get_order_status"),
        s("jsonrpc.request.id", "3"),
        session,
        proto,
    ]
    tool_client = _mcp_span(
        "tools/call get_order_status",
        "5e6f708192a3b4c5",
        1_000,
        1_610,
        tool_attrs,
        trace_id=t1,
        parent_span_id=agent["spanId"],
    )
    tool_server = _mcp_span(
        "tools/call get_order_status",
        "6f708192a3b4c5d6",
        1_010,
        1_600,
        tool_attrs,
        trace_id=t1,
        kind=2,  # SERVER
        parent_span_id=tool_client["spanId"],
    )
    # Sent by the MCP server while it works: the server initiates this one, so
    # per the conventions it is a CLIENT span on the server side.
    progress = _mcp_span(
        "notifications/progress",
        "708192a3b4c5d6e7",
        1_300,
        1_301,
        [s("mcp.method.name", "notifications/progress"), session, proto],
        trace_id=t1,
        parent_span_id=tool_server["spanId"],
    )
    failed = _mcp_span(
        "tools/call refund_order",
        "8192a3b4c5d6e7f8",
        3_000,
        3_650,
        [
            s("mcp.method.name", "tools/call"),
            s("gen_ai.operation.name", "execute_tool"),
            s("gen_ai.tool.name", "refund_order"),
            s("jsonrpc.request.id", "4"),
            s("error.type", "tool_error"),
            session,
            proto,
        ],
        trace_id=t2,
        status={"code": 2, "message": "refund window closed"},
    )
    return [
        {
            "resourceSpans": [
                _resource_spans(MCP_AGENT_RESOURCE, [agent, chat, prompt, resource, tool_client]),
                _resource_spans(MCP_SERVER_RESOURCE, [tool_server, progress]),
            ]
        },
        {"resourceSpans": [_resource_spans(MCP_AGENT_RESOURCE, [failed])]},
    ]
