"""oteru-hook: agent-IDE hook payloads -> OTLP spans on the integration surface (#90).

Payloads below are the documented examples of each vendor (2026-10-06):
- Antigravity: antigravity.google/docs/hooks (no event-name field: the event
  is the CLI argument; PreToolUse deliberately not handled — no neutral answer)
- Windsurf / Cascade: docs.devin.ai/desktop/cascade/hooks.md
- Cursor: cursor.com/docs/agent/hooks

Contract (README.md):
1. the hook never blocks the agent: exit 0 and the neutral stdout each IDE needs;
2. one agent turn = one trace, `invoke_agent <ide>` root, tool spans as children;
3. spans carry the GenAI/MCP attribute names the integration surface checks;
4. identity only from OTERU_USER_ID, content only with OTERU_CAPTURE_CONTENT=1;
5. sending failures are swallowed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import oteru_hook  # noqa: E402

NOW = 1_752_620_000_000_000_000
MS = 1_000_000

AG_COMMON = {
    "conversationId": "ec33ebf9-0cba-4100-8142-c61503f6c587",
    "workspacePaths": ["/workspace/project"],
    "transcriptPath": "/tmp/brain/x/.system_generated/logs/transcript.jsonl",
    "artifactDirectoryPath": "/tmp/brain/x",
    "modelName": "gemini-3.6-flash-medium",
}
WS_COMMON = {
    "trajectory_id": "traj-0001",
    "execution_id": "exec-0001",
    "timestamp": "2026-10-06T23:00:00Z",
    "model_name": "Claude Sonnet 4",
}
CU_COMMON = {
    "conversation_id": "conv-0001",
    "generation_id": "gen-0001",
    "model": "claude-sonnet-5-5",
    "cursor_version": "2.4.0",
    "workspace_roots": ["/project"],
    "user_email": None,
    "transcript_path": None,
}


@pytest.fixture
def run(tmp_path, monkeypatch):
    """run(ide, event, payload, at_ms=0, env={}) -> (stdout, spans)."""
    for var in ("OTERU_USER_ID", "OTERU_CAPTURE_CONTENT"):
        monkeypatch.delenv(var, raising=False)

    def _run(ide, event, payload, at_ms=0, env=None):
        stdout, request = oteru_hook.handle(
            ide, event, payload, now_ns=NOW + at_ms * MS, state_dir=tmp_path, env=env or {}
        )
        return stdout, spans_of(request)

    return _run


def spans_of(request):
    if not request:
        return []
    out = []
    for rs in request["resourceSpans"]:
        resource = {a["key"]: a["value"] for a in rs["resource"]["attributes"]}
        for ss in rs["scopeSpans"]:
            for sp in ss["spans"]:
                sp = dict(sp)
                sp["_resource"] = resource
                sp["_attrs"] = {a["key"]: next(iter(a["value"].values())) for a in sp["attributes"]}
                out.append(sp)
    return out


def duration_ms(span):
    return (int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"])) // MS


# --- 1. never block ------------------------------------------------------------------

NEUTRAL = [
    ("cursor", "preToolUse", {"permission": "allow"}),
    ("cursor", "beforeShellExecution", {"permission": "allow"}),
    ("cursor", "beforeMCPExecution", {"permission": "allow"}),
    ("cursor", "beforeReadFile", {"permission": "allow"}),
    ("cursor", "subagentStart", {"permission": "allow"}),
    ("cursor", "beforeSubmitPrompt", {"continue": True}),
    ("cursor", "postToolUse", {}),
    ("cursor", "stop", {}),
    ("antigravity", "PreInvocation", {}),
    ("antigravity", "PostToolUse", {}),
    ("antigravity", "PostInvocation", {"injectSteps": [], "terminationBehavior": ""}),
]


@pytest.mark.parametrize(("ide", "event", "expected"), NEUTRAL, ids=[f"{i}-{e}" for i, e, _ in NEUTRAL])
def test_neutral_stdout_never_changes_the_agents_decision(run, ide, event, expected):
    common = CU_COMMON if ide == "cursor" else AG_COMMON
    payload = dict(common, hook_event_name=event) if ide == "cursor" else dict(common, invocationNum=0)
    stdout, _ = run(ide, event, payload)
    assert json.loads(stdout) == expected


@pytest.mark.parametrize("event", ["pre_run_command", "post_run_command", "pre_user_prompt"])
def test_windsurf_hooks_print_nothing(run, event):
    stdout, _ = run("windsurf", event, dict(WS_COMMON, agent_action_name=event, tool_info={}))
    assert stdout == ""


def test_antigravity_pretooluse_is_refused_not_answered():
    """PreToolUse has no neutral decision; the installer must not register it."""
    assert "PreToolUse" not in oteru_hook.HANDLED["antigravity"]


def test_main_exits_zero_on_garbage_stdin(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("OTERU_HOOK_STATE", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("{not json"))
    assert oteru_hook.main(["cursor"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_send_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("OTERU_OTLP_ENDPOINT", "http://127.0.0.1:9")  # nothing listens
    oteru_hook.send({"resourceSpans": []}, timeout=0.2)  # must not raise


# --- 2 + 3. Cursor: stateless, from postToolUse durations ------------------------------


def test_cursor_tool_span_from_post_tool_use(run):
    _, spans = run(
        "cursor",
        "postToolUse",
        dict(
            CU_COMMON,
            hook_event_name="postToolUse",
            tool_name="Shell",
            tool_input={"command": "npm test"},
            tool_output='{"exitCode":0,"stdout":"ok"}',
            tool_use_id="abc123",
            cwd="/project",
            duration=5432,
        ),
    )
    (span,) = spans
    assert span["name"] == "execute_tool Shell"
    assert span["_attrs"]["gen_ai.operation.name"] == "execute_tool"
    assert span["_attrs"]["gen_ai.tool.name"] == "Shell"
    assert span["_attrs"]["gen_ai.tool.call.id"] == "abc123"
    assert span["_attrs"]["gen_ai.conversation.id"] == "conv-0001"
    assert duration_ms(span) == 5432
    assert span["status"]["code"] == 0
    assert span["_resource"]["service.name"] == {"stringValue": "cursor-hooks"}


def test_cursor_failure_carries_error_type(run):
    _, (span,) = run(
        "cursor",
        "postToolUseFailure",
        dict(
            CU_COMMON,
            hook_event_name="postToolUseFailure",
            tool_name="Shell",
            tool_use_id="abc124",
            error_message="Command timed out after 30s",
            failure_type="timeout",
            duration=30000,
        ),
    )
    assert span["status"] == {"code": 2, "message": "Command timed out after 30s"}
    assert span["_attrs"]["error.type"] == "timeout"


def test_cursor_turn_is_one_trace(run):
    run("cursor", "beforeSubmitPrompt", dict(CU_COMMON, hook_event_name="beforeSubmitPrompt", prompt="x"))
    _, (tool,) = run(
        "cursor",
        "postToolUse",
        dict(CU_COMMON, hook_event_name="postToolUse", tool_name="Read", tool_use_id="t1", duration=10),
        at_ms=1_000,
    )
    _, (turn,) = run(
        "cursor", "stop", dict(CU_COMMON, hook_event_name="stop", status="completed"), at_ms=4_000
    )
    assert turn["name"] == "invoke_agent cursor"
    assert turn["_attrs"]["gen_ai.operation.name"] == "invoke_agent"
    assert turn["_attrs"]["gen_ai.agent.name"] == "cursor"
    assert turn["_attrs"]["gen_ai.request.model"] == "claude-sonnet-5-5"
    assert duration_ms(turn) == 4_000
    assert tool["traceId"] == turn["traceId"]
    assert tool["parentSpanId"] == turn["spanId"]
    assert "parentSpanId" not in turn


def test_cursor_error_stop_marks_the_turn_failed(run):
    _, (turn,) = run("cursor", "stop", dict(CU_COMMON, hook_event_name="stop", status="error"))
    assert turn["status"]["code"] == 2
    assert turn["_attrs"]["error.type"] == "error"


def test_different_generations_are_different_traces(run):
    def tool(gen):
        _, (span,) = run(
            "cursor",
            "postToolUse",
            dict(CU_COMMON, generation_id=gen, hook_event_name="postToolUse", tool_name="Read", duration=1),
        )
        return span

    assert tool("gen-a")["traceId"] != tool("gen-b")["traceId"]


# --- Windsurf: pre/post pairing through state --------------------------------------------


def ws(event, tool_info, **kw):
    return dict(WS_COMMON, agent_action_name=event, tool_info=tool_info, **kw)


def test_windsurf_command_span_pairs_pre_and_post(run):
    info = {"command_line": "npm install left-pad", "cwd": "/Users/x/project"}
    _, nothing = run("windsurf", "pre_run_command", ws("pre_run_command", info))
    assert nothing == []
    _, (span,) = run("windsurf", "post_run_command", ws("post_run_command", info), at_ms=2_500)
    assert span["name"] == "execute_tool run_command"
    assert duration_ms(span) == 2_500
    assert span["_attrs"]["gen_ai.conversation.id"] == "traj-0001"


def test_windsurf_mcp_span_is_an_mcp_tools_call(run):
    info = {
        "mcp_server_name": "github",
        "mcp_tool_name": "create_issue",
        "mcp_tool_arguments": {"owner": "o", "repo": "r", "title": "t"},
    }
    run("windsurf", "pre_mcp_tool_use", ws("pre_mcp_tool_use", info))
    _, (span,) = run(
        "windsurf", "post_mcp_tool_use", ws("post_mcp_tool_use", dict(info, mcp_result="ok")), at_ms=800
    )
    assert span["name"] == "tools/call create_issue"
    assert span["_attrs"]["mcp.method.name"] == "tools/call"
    assert span["_attrs"]["gen_ai.tool.name"] == "create_issue"
    assert span["_attrs"]["gen_ai.operation.name"] == "execute_tool"
    assert span["_attrs"]["oteru.mcp.server.name"] == "github"
    assert duration_ms(span) == 800


def test_windsurf_repeated_identical_commands_pair_in_order(run):
    info = {"command_line": "make test", "cwd": "/p"}
    run("windsurf", "pre_run_command", ws("pre_run_command", info), at_ms=0)
    run("windsurf", "pre_run_command", ws("pre_run_command", info), at_ms=100)
    _, (first,) = run("windsurf", "post_run_command", ws("post_run_command", info), at_ms=1_000)
    _, (second,) = run("windsurf", "post_run_command", ws("post_run_command", info), at_ms=1_100)
    assert (duration_ms(first), duration_ms(second)) == (1_000, 1_000)


def test_windsurf_post_without_pre_still_emits_a_zero_length_span(run):
    _, (span,) = run("windsurf", "post_read_code", ws("post_read_code", {"file_path": "/p/a.py"}))
    assert span["name"] == "execute_tool read_code"
    assert duration_ms(span) == 0


def test_windsurf_turn_from_prompt_to_response(run):
    run("windsurf", "pre_user_prompt", ws("pre_user_prompt", {"user_prompt": "hi"}))
    _, (turn,) = run(
        "windsurf", "post_cascade_response", ws("post_cascade_response", {"response": "done"}), at_ms=7_000
    )
    assert turn["name"] == "invoke_agent windsurf"
    assert duration_ms(turn) == 7_000
    assert turn["_attrs"]["gen_ai.request.model"] == "Claude Sonnet 4"


# --- Antigravity: invocations through state, tools point-in-time ------------------------


def test_antigravity_invocation_and_tool_share_a_trace(run):
    run("antigravity", "PreInvocation", dict(AG_COMMON, invocationNum=3, initialNumSteps=10))
    _, (tool,) = run(
        "antigravity",
        "PostToolUse",
        dict(AG_COMMON, toolCall={"name": "run_command", "args": {"CommandLine": "npm test"}}, stepIdx=19),
        at_ms=1_200,
    )
    _, (turn,) = run(
        "antigravity", "PostInvocation", dict(AG_COMMON, invocationNum=3, initialNumSteps=10), at_ms=3_000
    )
    assert turn["name"] == "invoke_agent antigravity"
    assert duration_ms(turn) == 3_000
    assert tool["name"] == "execute_tool run_command"
    assert tool["traceId"] == turn["traceId"] and tool["parentSpanId"] == turn["spanId"]
    assert duration_ms(tool) == 0  # documented limit: no PreToolUse -> point-in-time


def test_antigravity_tool_error_marks_the_span(run):
    run("antigravity", "PreInvocation", dict(AG_COMMON, invocationNum=0))
    _, (tool,) = run(
        "antigravity",
        "PostToolUse",
        dict(AG_COMMON, toolCall={"name": "run_command", "args": {}}, stepIdx=5, error="exit status 1"),
    )
    assert tool["status"] == {"code": 2, "message": "exit status 1"}
    assert tool["_attrs"]["error.type"] == "tool_error"


# --- 4. identity and content are opt-in ---------------------------------------------------


def _cursor_tool(run, **env):
    _, (span,) = run(
        "cursor",
        "postToolUse",
        dict(
            CU_COMMON,
            user_email="someone@example.com",
            hook_event_name="postToolUse",
            tool_name="Shell",
            tool_input={"command": "cat secrets.txt"},
            tool_output="SECRET",
            duration=1,
        ),
        env=env,
    )
    return span


def test_no_identity_and_no_content_by_default(run):
    span = _cursor_tool(run)
    flat = json.dumps(span)
    assert "someone@example.com" not in flat
    assert "secrets.txt" not in flat and "SECRET" not in flat
    assert "enduser.id" not in span["_resource"]


def test_identity_from_env_only(run):
    span = _cursor_tool(run, OTERU_USER_ID="dev-42")
    assert span["_resource"]["enduser.id"] == {"stringValue": "dev-42"}


def test_content_capture_is_opt_in(run):
    span = _cursor_tool(run, OTERU_CAPTURE_CONTENT="1")
    assert json.loads(span["_attrs"]["gen_ai.tool.call.arguments"]) == {"command": "cat secrets.txt"}
    assert span["_attrs"]["gen_ai.tool.call.result"] == "SECRET"


# --- install snippets register exactly what the bridge handles ------------------------

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _registered(ide: str) -> set[str]:
    doc = json.loads((EXAMPLES / f"{ide}.hooks.json").read_text())
    if ide == "antigravity":
        return set(doc["oteru"])
    return set(doc["hooks"])


@pytest.mark.parametrize("ide", sorted(oteru_hook.HANDLED))
def test_example_hooks_json_registers_exactly_the_handled_events(ide):
    assert _registered(ide) == oteru_hook.HANDLED[ide]


def test_example_antigravity_commands_pass_the_event_name():
    doc = json.loads((EXAMPLES / "antigravity.hooks.json").read_text())["oteru"]
    for event, entries in doc.items():
        handlers = [h for e in entries for h in e.get("hooks", [e])]
        assert all(h["command"].endswith(f"antigravity {event}") for h in handlers)
