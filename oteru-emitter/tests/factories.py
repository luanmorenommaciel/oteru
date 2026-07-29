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
