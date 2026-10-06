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


# --- One fixture per emitter profile (#41) ----------------------------------------
#
# Each builder writes out the telemetry shape a tool documents for its native
# OTLP export — service.name, scope, event/span names, and the attribute keys
# its profile rotates or preserves. Sources are the tool's own code/docs, read
# 2026-10-06 (commit or URL in each docstring). Values are fabricated and
# identity is placeholder-only; nothing here is a capture.


def _profile_span(
    name: str,
    span_id: str,
    start_ms: int,
    end_ms: int,
    attributes: list[dict],
    *,
    trace_id: str,
    kind: int = 3,  # CLIENT
    parent_span_id: str | None = None,
) -> dict:
    out = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(BASE_NS + start_ms * MS),
        "endTimeUnixNano": str(BASE_NS + end_ms * MS),
        "attributes": attributes,
        "status": {"code": 0},
    }
    if parent_span_id is not None:
        out["parentSpanId"] = parent_span_id
    return out


def _resource(service: str, *extra: dict) -> dict:
    return {"attributes": [s("service.name", service), *extra]}


def _log_record(offset_ms: int, body: str, attributes: list[dict]) -> dict:
    ts = str(BASE_NS + offset_ms * MS)
    return {
        "timeUnixNano": ts,
        "observedTimeUnixNano": ts,
        "body": {"stringValue": body},
        "attributes": attributes,
    }


def _logs_batch(resource: dict, scope: dict, records: list[dict]) -> dict:
    return {
        "resourceLogs": [
            {"resource": resource, "scopeLogs": [{"scope": scope, "logRecords": records}]}
        ]
    }


def _traces_batch(resource: dict, scope: dict, spans: list[dict]) -> dict:
    return {
        "resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": scope, "spans": spans}]}]
    }


def _sum_batch(resource: dict, scope: dict, name: str, unit: str, points: list[dict]) -> dict:
    return {
        "resourceMetrics": [
            {
                "resource": resource,
                "scopeMetrics": [
                    {
                        "scope": scope,
                        "metrics": [
                            {
                                "name": name,
                                "unit": unit,
                                "sum": {
                                    "aggregationTemporality": 1,  # DELTA
                                    "isMonotonic": True,
                                    "dataPoints": points,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _point(offset_ms: int, value: int, attributes: list[dict]) -> dict:
    return {
        "startTimeUnixNano": str(BASE_NS),
        "timeUnixNano": str(BASE_NS + offset_ms * MS),
        "asInt": str(value),
        "attributes": attributes,
    }


def claude_code_capture() -> list[dict]:
    """Claude Code: the trace fixture above plus a log record carrying the
    per-prompt id (prompt.id) the profile rotates."""
    record = _log_record(
        5,
        "claude_code.user_prompt",
        [
            *IDENTITY,
            s("event.name", "user_prompt"),
            s("prompt.id", "5d7a1c3e-8b24-4f60-9e1a-2c4b6d8f0a13"),
        ],
    )
    events = _logs_batch(
        RESOURCE, {"name": "com.anthropic.claude_code.events", "version": "2.1.220"}, [record]
    )
    return [*traces_capture(), events]


def codex_capture() -> list[dict]:
    """OpenAI Codex CLI — openai/codex@19c4793, codex-rs/otel/src/events/shared.rs,
    session_telemetry.rs, metrics/names.rs. Event name in `event.name`; tracer
    scope = service name; meter scope "codex". The log scope (tracing target
    `codex_otel.log_only`) is unverified upstream — replay warns if it differs."""
    resource = _resource("codex_cli_rs", s("service.version", "0.130.0"), s("env", "dev"))
    common = [
        s("conversation.id", "0199b7a2-5c1e-7d40-8f3a-6b2e9c4d1a07"),
        s("app.version", "0.130.0"),
        s("originator", "codex_cli_rs"),
        s("terminal.type", "vscode"),
        s("model", "gpt-5-codex"),
        s("auth_mode", "ChatGPT"),
        s("user.account_id", "33333333-3333-3333-3333-333333333333"),
        s("user.email", "user@example.com"),
        s("turn.id", "0199b7a2-5d00-7a11-9c2b-1e3f5a7b9c0d"),
    ]
    logs = _logs_batch(
        resource,
        {"name": "codex_otel.log_only"},
        [
            _log_record(
                10,
                "",
                [
                    *common,
                    s("event.name", "codex.api_request"),
                    i("duration_ms", 812),
                    i("http.response.status_code", 200),
                ],
            ),
            _log_record(
                900,
                "",
                [
                    *common,
                    s("event.name", "codex.tool_result"),
                    s("tool_name", "shell"),
                    s("call_id", "call_01REDACTEDcodex0001"),
                    b("success", True),
                    i("duration_ms", 340),
                ],
            ),
        ],
    )
    spans = _traces_batch(
        resource,
        {"name": "codex_cli_rs"},
        [
            _profile_span(
                "op.dispatch.user_input",
                "aa01bb02cc03dd04",
                0,
                5,
                [s("submission.id", "0199b7a2-5cf0-7e22-8d4c-3a5b7c9d1e2f")],
                trace_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0",
                kind=1,
            ),
            _profile_span(
                "session_task.turn",
                "aa01bb02cc03dd05",
                5,
                1_400,
                [
                    s("thread.id", "0199b7a2-5c1e-7d40-8f3a-6b2e9c4d1a07"),
                    s("turn.id", "0199b7a2-5d00-7a11-9c2b-1e3f5a7b9c0d"),
                ],
                trace_id="0f1e2d3c4b5a69788796a5b4c3d2e1f0",
                kind=1,
                parent_span_id="aa01bb02cc03dd04",
            ),
        ],
    )
    metrics = _sum_batch(
        resource,
        {"name": "codex"},
        "codex.tool.call",
        "{call}",
        [_point(900, 1, [s("tool", "shell"), s("success", "true")])],
    )
    return [logs, spans, metrics]


def cursor_capture() -> list[dict]:
    """Cursor native OTLP export (Enterprise beta, sent by Cursor's servers) —
    cursor.com/docs/enterprise/opentelemetry-export (+ /wire). Logs + delta
    metrics only, no spans; scope cursor.telemetry 0.1.0; the event name is the
    log body; cursor.event.id is the dedup key, so replay must rotate it."""
    resource = _resource(
        "cursor",
        i("cursor.team.id", 4242),
        s("cursor.surface", "desktop"),
        s("cursor.entrypoint", "desktop"),
        s("cursor.user.id", "user_REDACTED_0002"),
        s("cursor.user.account_id", "acct_REDACTED_0002"),
        s("cursor.user.email", "user@example.com"),
    )
    scope = {"name": "cursor.telemetry", "version": "0.1.0"}
    logs = _logs_batch(
        resource,
        scope,
        [
            _log_record(
                10,
                "api_request",
                [
                    s("cursor.event.id", "evt_7f3a9c1e5b2d4086"),
                    s("cursor.source_event.id", "src_1c9e7b52d04a4f3e"),
                    s("cursor.request.id", "8e2f4a6c-1b3d-4e5f-9a7b-0c2d4e6f8a1b"),
                    s("cursor.conversation.id", "3a5c7e9b-2d4f-4a6c-8e0b-1d3f5a7c9e2b"),
                    s("cursor.usage_event.id", "use_4b6d8f0a2c4e6a8c"),
                    s("cursor.model.name", "claude-sonnet-5-5"),
                    i("cursor.api.request.input_tokens", 2410),
                    i("cursor.api.request.output_tokens", 388),
                    b("cursor.api.billable", True),
                ],
            )
        ],
    )
    metrics = _sum_batch(
        resource,
        scope,
        "cursor.token.usage",
        "{token}",
        [
            _point(
                10,
                2410,
                [s("cursor.token.type", "input"), s("cursor.model.name", "claude-sonnet-5-5")],
            )
        ],
    )
    return [logs, metrics]


def copilot_chat_capture() -> list[dict]:
    """GitHub Copilot Chat in VS Code — microsoft/vscode@9b2d899,
    extensions/copilot/docs/monitoring/agent_monitoring.md and
    src/platform/otel/common/genAiAttributes.ts. GenAI semconv spans
    invoke_agent -> chat / execute_tool; service.name and scope copilot-chat;
    resource session.id is per VS Code window; user identity only with
    captureIdentity on (user.name on invoke_agent, process.user.name on the
    resource)."""
    resource = _resource(
        "copilot-chat",
        s("service.version", "0.42.0"),
        s("session.id", "6b8d0f2a-4c6e-4a8c-9e1b-3d5f7a9c1e4d"),
        s("process.user.name", "user_REDACTED_0003"),
    )
    trace = "2c4e6a8c0e2a4c6e8a0c2e4a6c8e0a2c"
    conversation = s("gen_ai.conversation.id", "9a1c3e5b-7d9f-4b1d-8f3a-5c7e9b1d3f5a")
    session = s("copilot_chat.chat_session_id", "1e3a5c7e-9b2d-4f6a-8c0e-2a4c6e8a0c2e")
    agent = _profile_span(
        "invoke_agent GitHub Copilot Chat",
        "b1c2d3e4f5a6b7c8",
        0,
        3_200,
        [
            s("gen_ai.operation.name", "invoke_agent"),
            s("gen_ai.provider.name", "github"),
            s("gen_ai.agent.name", "GitHub Copilot Chat"),
            s("user.name", "user_REDACTED_0003"),
            conversation,
            session,
        ],
        trace_id=trace,
        kind=1,
    )
    chat = _profile_span(
        "chat gpt-5",
        "b1c2d3e4f5a6b7c9",
        30,
        1_500,
        [
            s("gen_ai.operation.name", "chat"),
            s("gen_ai.provider.name", "github"),
            s("gen_ai.request.model", "gpt-5"),
            s("gen_ai.response.id", "chatcmpl-REDACTEDcopilot0001"),
            i("gen_ai.usage.input_tokens", 5120),
            i("gen_ai.usage.output_tokens", 230),
            conversation,
        ],
        trace_id=trace,
        parent_span_id=agent["spanId"],
    )
    tool = _profile_span(
        "execute_tool readFile",
        "b1c2d3e4f5a6b7d0",
        1_520,
        1_700,
        [
            s("gen_ai.operation.name", "execute_tool"),
            s("gen_ai.tool.name", "readFile"),
            s("gen_ai.tool.type", "function"),
            s("gen_ai.tool.call.id", "call_REDACTEDcopilot0001"),
            conversation,
        ],
        trace_id=trace,
        kind=1,
        parent_span_id=agent["spanId"],
    )
    return [
        _traces_batch(resource, {"name": "copilot-chat", "version": "0.42.0"}, [agent, chat, tool])
    ]


def litellm_capture() -> list[dict]:
    """LiteLLM proxy with callbacks: ["otel"] — BerriAI/litellm@d8bc2b78e4ab,
    litellm/integrations/opentelemetry.py. Default (non-semconv-mode) span
    names; principal identity rides as metadata.user_api_key_* span
    attributes; cost as gen_ai.cost.*."""
    resource = _resource(
        "litellm", s("deployment.environment", "production"), s("model_id", "litellm")
    )
    trace = "3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3c"
    server = _profile_span(
        "Received Proxy Server Request",
        "c1d2e3f4a5b6c7d8",
        0,
        1_250,
        [s("http.route", "/chat/completions"), i("http.response.status_code", 200)],
        trace_id=trace,
        kind=2,
    )
    request = _profile_span(
        "litellm_request",
        "c1d2e3f4a5b6c7d9",
        15,
        1_240,
        [
            s("gen_ai.system", "openai"),
            s("gen_ai.request.model", "gpt-4.1"),
            s("gen_ai.response.id", "chatcmpl-REDACTEDlitellm0001"),
            i("gen_ai.usage.input_tokens", 820),
            i("gen_ai.usage.output_tokens", 96),
            s("llm.request.type", "acompletion"),
            s("litellm.call_id", "4f6a8c0e-2b4d-4f6a-8c0e-2b4d6f8a0c2e"),
            s("metadata.user_api_key_hash", "hash_REDACTED_0004"),
            s("metadata.user_api_key_user_id", "user_REDACTED_0004"),
            s("metadata.user_api_key_user_email", "user@example.com"),
            s("metadata.user_api_key_team_id", "team_REDACTED_0004"),
            {"key": "gen_ai.cost.total_cost", "value": {"doubleValue": 0.00214}},
        ],
        trace_id=trace,
        kind=1,
        parent_span_id=server["spanId"],
    )
    return [_traces_batch(resource, {"name": "litellm"}, [server, request])]


def crewai_capture() -> list[dict]:
    """CrewAI native event tracing (telemetry_session with an OTLP exporter) —
    crewAIInc/crewAI@e836a191, lib/crewai/src/crewai/telemetry/tracing/
    (handlers.py, semantic_conventions.py, session.py). GenAI semconv; note
    gen_ai.agent.name is an MD5 key, the role lives in crewai.agent.role.
    Principal identity only when the host passes principal=."""
    resource = _resource("crewai")
    scope = {"name": "crewai", "version": "1.4.0"}
    trace = "4e6a8c0e2a4c6e8a0c2e4a6c8e0a2c4e"
    run = s("crewai.execution_uuid", "7c9e1b3d-5f7a-4c9e-8b1d-3f5a7c9e1b3d")
    principal = [
        s("crewai.principal.type", "user"),
        s("crewai.principal.id", "user_REDACTED_0005"),
        s("enduser.id", "user_REDACTED_0005"),
    ]
    crew = _profile_span(
        "execute crew",
        "d1e2f3a4b5c6d7e8",
        0,
        6_000,
        [
            s("gen_ai.operation.name", "invoke_workflow"),
            s("gen_ai.workflow.name", "support_crew"),
            s("crewai.crew.id", "2b4d6f8a-0c2e-4a6c-8e0a-2c4e6a8c0e2a"),
            run,
            *principal,
        ],
        trace_id=trace,
        kind=1,
    )
    task = _profile_span(
        "execute task",
        "d1e2f3a4b5c6d7e9",
        10,
        5_900,
        [
            s("gen_ai.operation.name", "execute_task"),
            s("crewai.task.id", "5d7f9b1d-3f5a-4c7e-9b1d-3f5a7c9e1b3d"),
            run,
        ],
        trace_id=trace,
        kind=1,
        parent_span_id=crew["spanId"],
    )
    agent = _profile_span(
        "execute agent",
        "d1e2f3a4b5c6d7f0",
        20,
        5_800,
        [
            s("gen_ai.operation.name", "invoke_agent"),
            s("gen_ai.agent.name", "9f2c6d1e8b3a4f5c7d9e0a1b2c3d4e5f"),
            s("crewai.agent.role", "Support Analyst"),
            s("gen_ai.conversation.id", "8b0d2f4a-6c8e-4b0d-9f2a-4c6e8a0c2e4b"),
            run,
        ],
        trace_id=trace,
        kind=1,
        parent_span_id=task["spanId"],
    )
    llm = _profile_span(
        "call llm",
        "d1e2f3a4b5c6d7f1",
        40,
        2_100,
        [
            s("gen_ai.operation.name", "chat"),
            s("gen_ai.provider.name", "openai"),
            s("gen_ai.request.model", "gpt-4.1-mini"),
            i("gen_ai.usage.input_tokens", 1450),
            i("gen_ai.usage.output_tokens", 210),
            run,
        ],
        trace_id=trace,
        parent_span_id=agent["spanId"],
    )
    tool = _profile_span(
        "call tool",
        "d1e2f3a4b5c6d7f2",
        2_150,
        2_700,
        [s("gen_ai.operation.name", "execute_tool"), s("gen_ai.tool.name", "search_orders"), run],
        trace_id=trace,
        kind=1,
        parent_span_id=agent["spanId"],
    )
    return [_traces_batch(resource, scope, [crew, task, agent, llm, tool])]


# profile name -> builder; tests/test_emitter_profiles.py enumerates it.
PROFILE_FIXTURES = {
    "claude_code": claude_code_capture,
    "codex": codex_capture,
    "copilot_chat": copilot_chat_capture,
    "cursor": cursor_capture,
    "crewai": crewai_capture,
    "litellm": litellm_capture,
}
