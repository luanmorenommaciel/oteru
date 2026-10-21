"""Minimum Integration Surface checker (#22).

The surface (docs/integration-surface.md) is the contract an agent meets to
be observable by Oteru regardless of its stack: OTLP transport plus the OTel
GenAI and MCP semantic conventions. This module turns that table into code,
so "does my agent conform?" is a command, not a reading exercise:

    oteru-emitter check capture.json

A span is *on the surface* when it declares what it is — a GenAI operation
(``gen_ai.operation.name``) or an MCP call (``mcp.method.name``). Anything else
is *outside*: not wrong, just not observable through the contract (Claude
Code's native ``claude_code.*`` spans are the example — a profile maps them,
#41). Rules only judge spans on the surface.

Stdlib-only: checks the OTLP/JSON capture as written, no protobuf needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

INFERENCE_OPERATIONS = frozenset({"chat", "text_completion", "generate_content", "embeddings"})
AGENT_OPERATIONS = frozenset({"invoke_agent", "create_agent"})
# MCP methods whose span must name a resource / prompt (semconv: conditionally
# required "when the request includes a resource URI" / "is related to a prompt").
MCP_RESOURCE_METHODS = frozenset({"resources/read", "resources/subscribe", "resources/unsubscribe"})
MCP_PROMPT_METHODS = frozenset({"prompts/get"})
STATUS_ERROR = 2


@dataclass(frozen=True)
class Finding:
    level: str  # "error" (contract broken) | "warning" (recommended, absent)
    span: str  # span name, or "<resource>"
    attribute: str
    message: str


@dataclass
class Report:
    on_surface: int = 0
    outside: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]


def _attr_map(attributes: list[dict]) -> dict[str, object]:
    """OTLP attribute list -> {key: raw AnyValue content}. Presence is what
    the rules check; the value only matters for the two discriminators."""
    out: dict[str, object] = {}
    for entry in attributes or []:
        value = entry.get("value") or {}
        out[entry.get("key", "")] = next(iter(value.values()), None) if value else None
    return out


def _check_span(span: dict, report: Report) -> None:
    attrs = _attr_map(span.get("attributes", []))
    name = span.get("name", "<unnamed>")
    operation = attrs.get("gen_ai.operation.name")
    method = attrs.get("mcp.method.name")
    if operation is None and method is None:
        report.outside += 1
        return
    report.on_surface += 1

    def need(attribute: str, level: str, why: str) -> None:
        if attribute not in attrs:
            report.findings.append(Finding(level, name, attribute, why))

    if operation in INFERENCE_OPERATIONS:
        need("gen_ai.provider.name", "error", "required on inference spans")
        need("gen_ai.request.model", "warning", "required on inference spans when available")
        need("gen_ai.usage.input_tokens", "warning", "recommended: cost needs it")
        need("gen_ai.usage.output_tokens", "warning", "recommended: cost needs it")
    elif operation == "execute_tool":
        need("gen_ai.tool.name", "error", "required on execute_tool spans")
    elif operation in AGENT_OPERATIONS:
        need("gen_ai.agent.name", "warning", "required on agent spans when available")

    if method is not None:
        method = str(method)
        if method == "tools/call":
            need("gen_ai.tool.name", "error", "required on MCP tools/call spans")
            if operation != "execute_tool":
                need("gen_ai.operation.name", "warning", "should be execute_tool on tools/call")
        elif method in MCP_RESOURCE_METHODS:
            need("mcp.resource.uri", "error", f"required on MCP {method} spans")
        elif method in MCP_PROMPT_METHODS:
            need("gen_ai.prompt.name", "error", f"required on MCP {method} spans")
        if not method.startswith("notifications/"):
            need("jsonrpc.request.id", "warning", "required on MCP requests (not notifications)")

    if (span.get("status") or {}).get("code") == STATUS_ERROR:
        need("error.type", "error", "required when the span failed")


def check_payloads(payloads: list[dict]) -> Report:
    """Checks OTLP/JSON batches; non-trace batches are ignored."""
    report = Report()
    for payload in payloads:
        for resource_spans in payload.get("resourceSpans", []):
            resource = _attr_map((resource_spans.get("resource") or {}).get("attributes", []))
            if "service.name" not in resource:
                report.findings.append(
                    Finding(
                        "error",
                        "<resource>",
                        "service.name",
                        "required: identifies the agent/service",
                    )
                )
            for scope_spans in resource_spans.get("scopeSpans", []):
                for span in scope_spans.get("spans", []):
                    _check_span(span, report)
    return report
