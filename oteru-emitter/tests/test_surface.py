"""Minimum Integration Surface checker (#22).

Every rule in docs/integration-surface.md has a test named after it here, and
the expected findings are written from that table — never read off ``check``.
Each rule is exercised both ways: the attribute missing (finding) and present
(no finding), so a rule that never fires cannot pass.
"""

from __future__ import annotations

import json

import pytest

from oteru_emitter.cli import main
from oteru_emitter.surface import check_payloads


def _attrs(mapping: dict) -> list[dict]:
    return [{"key": k, "value": {"stringValue": str(v)}} for k, v in mapping.items()]


def _payload(span_attrs: dict, *, status: int = 0, resource: dict | None = None) -> dict:
    resource = {"service.name": "agent"} if resource is None else resource
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs(resource)},
                "scopeSpans": [
                    {
                        "scope": {"name": "test"},
                        "spans": [
                            {
                                "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
                                "spanId": "a1b2c3d4e5f60718",
                                "name": "span-under-test",
                                "attributes": _attrs(span_attrs),
                                "status": {"code": status},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _findings(span_attrs: dict, **kwargs) -> set[tuple[str, str]]:
    """(level, missing attribute) pairs for one span."""
    report = check_payloads([_payload(span_attrs, **kwargs)])
    return {(f.level, f.attribute) for f in report.findings}


INFERENCE_OK = {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": "anthropic",
    "gen_ai.request.model": "m",
    "gen_ai.usage.input_tokens": 1,
    "gen_ai.usage.output_tokens": 1,
}
TOOL_CALL_OK = {
    "mcp.method.name": "tools/call",
    "gen_ai.operation.name": "execute_tool",
    "gen_ai.tool.name": "get_order",
    "jsonrpc.request.id": "1",
}


# --- classification ------------------------------------------------------------


@pytest.mark.parametrize("op", ["chat", "text_completion", "generate_content", "embeddings"])
def test_every_inference_operation_is_on_the_surface(op):
    report = check_payloads([_payload(dict(INFERENCE_OK, **{"gen_ai.operation.name": op}))])
    assert report.on_surface == 1 and not report.findings


def test_span_without_gen_ai_or_mcp_attributes_is_outside_the_surface():
    report = check_payloads([_payload({"http.method": "GET"})])
    assert (report.on_surface, report.outside) == (0, 1)
    assert not report.findings


def test_claude_code_native_spans_are_outside_the_surface():
    """claude_code.* spans carry gen_ai.request.model but no gen_ai.operation.name:
    the surface keys on the operation, so they need a profile mapping (#41)."""
    report = check_payloads([_payload({"gen_ai.request.model": "m", "model": "m"})])
    assert report.on_surface == 0


# --- resource ------------------------------------------------------------------


def test_resource_requires_service_name():
    assert ("error", "service.name") in _findings(INFERENCE_OK, resource={})
    assert ("error", "service.name") not in _findings(INFERENCE_OK)


# --- one rule per row of the contract table, both ways -------------------------

# (rule id, span attributes that satisfy everything, attribute to drop, level)
RULES = [
    ("inference-provider", INFERENCE_OK, "gen_ai.provider.name", "error"),
    ("inference-model", INFERENCE_OK, "gen_ai.request.model", "warning"),
    ("inference-input-tokens", INFERENCE_OK, "gen_ai.usage.input_tokens", "warning"),
    ("inference-output-tokens", INFERENCE_OK, "gen_ai.usage.output_tokens", "warning"),
    (
        "execute-tool-name",
        {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "t"},
        "gen_ai.tool.name",
        "error",
    ),
    (
        "agent-name",
        {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "a"},
        "gen_ai.agent.name",
        "warning",
    ),
    ("mcp-tools-call-tool-name", TOOL_CALL_OK, "gen_ai.tool.name", "error"),
    ("mcp-tools-call-operation", TOOL_CALL_OK, "gen_ai.operation.name", "warning"),
    ("mcp-request-id", TOOL_CALL_OK, "jsonrpc.request.id", "warning"),
    (
        "mcp-resources-read-uri",
        {
            "mcp.method.name": "resources/read",
            "mcp.resource.uri": "file:///x",
            "jsonrpc.request.id": 1,
        },
        "mcp.resource.uri",
        "error",
    ),
    (
        "mcp-prompts-get-name",
        {"mcp.method.name": "prompts/get", "gen_ai.prompt.name": "p", "jsonrpc.request.id": 1},
        "gen_ai.prompt.name",
        "error",
    ),
]


@pytest.mark.parametrize(
    ("complete", "dropped", "level"), [r[1:] for r in RULES], ids=[r[0] for r in RULES]
)
def test_rule_fires_only_when_its_attribute_is_missing(complete, dropped, level):
    assert _findings(complete) == set()
    incomplete = {k: v for k, v in complete.items() if k != dropped}
    assert (level, dropped) in _findings(incomplete)


def test_mcp_notification_needs_no_request_id():
    assert _findings({"mcp.method.name": "notifications/progress"}) == set()


@pytest.mark.parametrize("complete", [INFERENCE_OK, TOOL_CALL_OK], ids=["inference", "mcp"])
def test_failed_span_requires_error_type(complete):
    assert ("error", "error.type") in _findings(complete, status=2)
    assert _findings(dict(complete, **{"error.type": "timeout"}), status=2) == set()


def test_findings_name_the_span():
    report = check_payloads([_payload({"gen_ai.operation.name": "chat"})])
    assert report.findings and all(f.span == "span-under-test" for f in report.findings)


# --- CLI -------------------------------------------------------------------------


def _write(tmp_path, payloads):
    path = tmp_path / "capture.json"
    path.write_text("".join(json.dumps(p) + "\n" for p in payloads), encoding="utf-8")
    return str(path)


def test_cli_check_passes_a_conforming_capture(tmp_path, capsys):
    assert main(["check", _write(tmp_path, [_payload(INFERENCE_OK)])]) == 0
    assert "1 span(s) on the surface" in capsys.readouterr().out


def test_cli_check_fails_on_errors(tmp_path, capsys):
    assert main(["check", _write(tmp_path, [_payload({"gen_ai.operation.name": "chat"})])]) == 1
    assert "gen_ai.provider.name" in capsys.readouterr().out


def test_cli_check_warnings_pass_unless_strict(tmp_path):
    no_tokens = {k: v for k, v in INFERENCE_OK.items() if "usage" not in k}
    path = _write(tmp_path, [_payload(no_tokens)])
    assert main(["check", path]) == 0
    assert main(["check", path, "--strict"]) == 1


def test_cli_check_fails_when_nothing_is_on_the_surface(tmp_path, capsys):
    assert main(["check", _write(tmp_path, [_payload({"http.method": "GET"})])]) == 1
    assert "no span" in capsys.readouterr().out


def test_cli_check_committed_sample_has_no_spans(sample_path, capsys):
    """The Claude Code sample carries logs + metrics only: nothing to check."""
    assert main(["check", str(sample_path)]) == 1
    assert "no span" in capsys.readouterr().out


def test_cli_check_factory_traces_are_outside_the_surface(traces_path, capsys):
    assert main(["check", str(traces_path)]) == 1
    assert "6 outside" in capsys.readouterr().out
