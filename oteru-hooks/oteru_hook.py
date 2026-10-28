#!/usr/bin/env python3
"""oteru-hook — agent-IDE hooks -> OTLP spans on the integration surface (#90).

Google Antigravity and Windsurf (Cascade) export no OpenTelemetry; Cursor's
export is Enterprise-only and server-side. All three run a command on every
agent action and hand it a JSON payload on stdin. Register this file as that
command and each agent turn becomes an OTLP trace:

    invoke_agent <ide>                 one agent turn (gen_ai.operation.name)
    ├── execute_tool <tool>            a built-in tool call
    └── tools/call <tool>              an MCP tool call (mcp.method.name)

Usage (see README.md for each IDE's hooks.json):

    oteru_hook.py cursor               # event read from hook_event_name
    oteru_hook.py windsurf             # event read from agent_action_name
    oteru_hook.py antigravity <Event>  # Antigravity sends no event name

Rules, in priority order:
1. Never block or alter the agent: always exit 0 and print the neutral answer
   the IDE expects (Cursor *blocks* on invalid JSON from permission hooks).
2. Stdlib only, one file — it runs on every agent action on a dev machine.
3. Identity only from OTERU_USER_ID; prompts, arguments and outputs only with
   OTERU_CAPTURE_CONTENT=1.
4. Telemetry failures are swallowed (short timeout, no retries).

Env: OTERU_OTLP_ENDPOINT (or OTEL_EXPORTER_OTLP_ENDPOINT, default
http://localhost:4318), OTERU_HOOK_STATE (default ~/.cache/oteru-hooks),
OTERU_HOOK_TIMEOUT (seconds, default 1), OTERU_USER_ID, OTERU_CAPTURE_CONTENT.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

VERSION = "0.1.0"

KIND_INTERNAL, KIND_CLIENT = 1, 3
STATUS_UNSET, STATUS_ERROR = 0, 2

# Events each IDE should register. Antigravity's PreToolUse is left out on
# purpose: every legal answer to it changes the agent's permissions.
HANDLED = {
    "antigravity": {"PreInvocation", "PostInvocation", "PostToolUse"},
    "windsurf": {
        "pre_user_prompt",
        "post_cascade_response",
        "pre_run_command",
        "post_run_command",
        "pre_read_code",
        "post_read_code",
        "pre_write_code",
        "post_write_code",
        "pre_mcp_tool_use",
        "post_mcp_tool_use",
    },
    "cursor": {"beforeSubmitPrompt", "postToolUse", "postToolUseFailure", "stop"},
}

CURSOR_PERMISSION_HOOKS = {
    "preToolUse",
    "beforeShellExecution",
    "beforeMCPExecution",
    "beforeReadFile",
    "subagentStart",
}


def neutral_stdout(ide: str, event: str) -> str:
    """The answer that leaves the agent's behaviour exactly as it was."""
    if ide == "cursor":
        if event in CURSOR_PERMISSION_HOOKS:
            return json.dumps({"permission": "allow"})
        if event == "beforeSubmitPrompt":
            return json.dumps({"continue": True})
        return "{}"
    if ide == "antigravity":
        if event == "PostInvocation":
            return json.dumps({"injectSteps": [], "terminationBehavior": ""})
        return "{}"
    return ""  # windsurf: exit code only


# --- ids and state ----------------------------------------------------------------


def _h(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def trace_id(ide: str, conv: str, turn: str) -> str:
    return _h("trace", ide, conv, turn)[:32]


def turn_span_id(ide: str, conv: str, turn: str) -> str:
    return _h("turn", ide, conv, turn)[:16]


class State:
    """Tiny per-conversation JSON files: turn starts and pending tool starts.

    Each hook is a separate process, so pairing a pre event with its post event
    needs somewhere to remember the start time. Best effort by design: a lost
    file only costs a span its duration, never the agent anything.
    """

    def __init__(self, root: Path, ide: str, conv: str):
        self.dir = Path(root) / ide / _h(conv)[:24]

    def _path(self, name: str) -> Path:
        return self.dir / f"{_h(name)[:24]}.json"

    def get(self, name: str, default=None):
        try:
            return json.loads(self._path(name).read_text())
        except (OSError, ValueError):
            return default

    def put(self, name: str, value) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(name).write_text(json.dumps(value))
        except OSError:
            pass

    def pop(self, name: str, default=None):
        value = self.get(name, default)
        try:
            self._path(name).unlink()
        except OSError:
            pass
        return value

    def push_start(self, key: str, ns: int) -> None:
        self.put("tool:" + key, [*self.get("tool:" + key, []), ns])

    def pop_start(self, key: str) -> int | None:
        starts = self.get("tool:" + key, [])
        if not starts:
            return None
        first, rest = starts[0], starts[1:]
        if rest:
            self.put("tool:" + key, rest)
        else:
            self.pop("tool:" + key)
        return first


# --- OTLP/JSON building -------------------------------------------------------------


def _attr(key: str, value) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _span(
    name: str,
    trace: str,
    span_id: str,
    start_ns: int,
    end_ns: int,
    attrs: dict,
    *,
    parent: str | None = None,
    kind: int = KIND_INTERNAL,
    error: tuple[str, str] | None = None,
) -> dict:
    span = {
        "traceId": trace,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(max(end_ns, start_ns)),
        "attributes": [_attr(k, v) for k, v in attrs.items() if v not in (None, "")],
        "status": {"code": STATUS_UNSET},
    }
    if parent:
        span["parentSpanId"] = parent
    if error:
        error_type, message = error
        span["status"] = {"code": STATUS_ERROR, "message": message}
        span["attributes"].append(_attr("error.type", error_type))
    return span


def _request(ide: str, spans: list[dict], env) -> dict | None:
    if not spans:
        return None
    resource = {
        "service.name": f"{ide}-hooks",
        "oteru.bridge": "hooks",
        "oteru.bridge.version": VERSION,
        "enduser.id": env.get("OTERU_USER_ID") or None,
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr(k, v) for k, v in resource.items() if v]},
                "scopeSpans": [{"scope": {"name": "oteru.hooks", "version": VERSION}, "spans": spans}],
            }
        ]
    }


# --- normalisation: one IDE payload -> turn / tool events ---------------------------


class Ctx:
    def __init__(self, ide, conv, turn, model, now_ns, state, env):
        self.ide, self.conv, self.turn, self.model = ide, conv, turn, model
        self.now_ns, self.state, self.env = now_ns, state, env
        self.capture = env.get("OTERU_CAPTURE_CONTENT") in ("1", "true", "yes")

    @property
    def trace(self) -> str:
        return trace_id(self.ide, self.conv, self.turn)

    @property
    def turn_id(self) -> str:
        return turn_span_id(self.ide, self.conv, self.turn)

    def turn_start(self) -> None:
        self.state.put("turn:" + self.turn, self.now_ns)

    def turn_end(self, error: tuple[str, str] | None = None) -> dict:
        start = self.state.pop("turn:" + self.turn) or self.now_ns
        model = self.model if self.model and self.model != "Unknown" else None
        attrs = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": self.ide,
            "gen_ai.conversation.id": self.conv,
            "gen_ai.request.model": model,
        }
        name = f"invoke_agent {self.ide}"
        return _span(name, self.trace, self.turn_id, start, self.now_ns, attrs, error=error)

    def tool(
        self,
        tool: str,
        start_ns: int,
        *,
        call_id: str | None = None,
        mcp_server: str | None = None,
        arguments=None,
        result=None,
        error: tuple[str, str] | None = None,
    ) -> dict:
        attrs = {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool,
            "gen_ai.tool.call.id": call_id,
            "gen_ai.conversation.id": self.conv,
            "gen_ai.agent.name": self.ide,
        }
        if mcp_server is not None:
            attrs["mcp.method.name"] = "tools/call"
            attrs["oteru.mcp.server.name"] = mcp_server
        if self.capture:
            if arguments is not None:
                attrs["gen_ai.tool.call.arguments"] = (
                    arguments if isinstance(arguments, str) else json.dumps(arguments, sort_keys=True)
                )
            if result is not None:
                attrs["gen_ai.tool.call.result"] = result if isinstance(result, str) else json.dumps(result)
        name = f"tools/call {tool}" if mcp_server is not None else f"execute_tool {tool}"
        return _span(
            name,
            self.trace,
            os.urandom(8).hex(),
            start_ns,
            self.now_ns,
            attrs,
            parent=self.turn_id,
            kind=KIND_CLIENT if mcp_server is not None else KIND_INTERNAL,
            error=error,
        )


def _cursor(event: str, p: dict, ctx: Ctx) -> list[dict]:
    if event == "beforeSubmitPrompt":
        ctx.turn_start()
        return []
    if event == "stop":
        status = p.get("status")
        return [ctx.turn_end(error=("error", "turn ended with status error") if status == "error" else None)]
    if event in ("postToolUse", "postToolUseFailure"):
        duration_ns = int(p.get("duration") or 0) * 1_000_000
        error = None
        if event == "postToolUseFailure":
            error = (p.get("failure_type") or "error", p.get("error_message") or "")
        return [
            ctx.tool(
                p.get("tool_name") or "unknown",
                ctx.now_ns - duration_ns,
                call_id=p.get("tool_use_id"),
                arguments=p.get("tool_input"),
                result=p.get("tool_output"),
                error=error,
            )
        ]
    return []


WINDSURF_TOOLS = {"run_command": "command_line", "read_code": "file_path", "write_code": "file_path"}


def _windsurf(event: str, p: dict, ctx: Ctx) -> list[dict]:
    info = p.get("tool_info") or {}
    if event == "pre_user_prompt":
        ctx.turn_start()
        return []
    if event == "post_cascade_response":
        return [ctx.turn_end()]
    phase, _, action = event.partition("_")
    if action == "mcp_tool_use":
        tool, server = info.get("mcp_tool_name") or "unknown", info.get("mcp_server_name") or ""
        key = json.dumps([ctx.turn, "mcp", server, tool, info.get("mcp_tool_arguments")], sort_keys=True)
    elif action in WINDSURF_TOOLS:
        tool, server = action, None
        key = json.dumps(
            [ctx.turn, action, info.get(WINDSURF_TOOLS[action]), info.get("cwd")], sort_keys=True
        )
    else:
        return []
    if phase == "pre":
        ctx.state.push_start(key, ctx.now_ns)
        return []
    start = ctx.state.pop_start(key)
    arguments = info.get("mcp_tool_arguments") if server is not None else {k: v for k, v in info.items()}
    return [
        ctx.tool(
            tool,
            start if start is not None else ctx.now_ns,
            mcp_server=server,
            arguments=arguments,
            result=info.get("mcp_result"),
        )
    ]


def _antigravity(event: str, p: dict, ctx: Ctx) -> list[dict]:
    if event == "PreInvocation":
        ctx.state.put("current-turn", ctx.turn)
        ctx.turn_start()
        return []
    if event == "PostInvocation":
        return [ctx.turn_end()]
    if event == "PostToolUse":
        call = p.get("toolCall") or {}
        error = p.get("error")
        return [
            ctx.tool(
                call.get("name") or "unknown",
                ctx.now_ns,  # no PreToolUse (no neutral answer) -> point-in-time
                arguments=call.get("args"),
                error=("tool_error", error) if error else None,
            )
        ]
    return []


ADAPTERS = {"cursor": _cursor, "windsurf": _windsurf, "antigravity": _antigravity}


def _identity(ide: str, event: str, p: dict, state_root: Path) -> tuple[str, str, str]:
    """(conversation, turn, model) per IDE."""
    if ide == "cursor":
        return p.get("conversation_id") or "", p.get("generation_id") or "", p.get("model") or ""
    if ide == "windsurf":
        return p.get("trajectory_id") or "", p.get("execution_id") or "", p.get("model_name") or ""
    conv = p.get("conversationId") or ""
    if "invocationNum" in p:
        turn = str(p["invocationNum"])
    else:  # tool events carry no invocation number: use the one in progress
        turn = State(state_root, ide, conv).get("current-turn", "") or ""
    return conv, turn, p.get("modelName") or ""


def handle(
    ide: str, event: str, payload: dict, *, now_ns: int, state_dir: Path, env
) -> tuple[str, dict | None]:
    """Pure core: (stdout to print, OTLP/JSON ExportTraceServiceRequest or None)."""
    stdout = neutral_stdout(ide, event)
    adapter = ADAPTERS.get(ide)
    if adapter is None or not isinstance(payload, dict):
        return stdout, None
    conv, turn, model = _identity(ide, event, payload, Path(state_dir))
    ctx = Ctx(ide, conv, turn, model, now_ns, State(Path(state_dir), ide, conv), env)
    return stdout, _request(ide, adapter(event, payload, ctx), env)


def send(request: dict, timeout: float = 1.0) -> None:
    base = (
        os.environ.get("OTERU_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or "http://localhost:4318"
    ).rstrip("/")
    req = urllib.request.Request(
        base + "/v1/traces",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except Exception:  # noqa: BLE001 — telemetry must never hurt the agent
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ide = argv[0] if argv else ""
    event = argv[1] if len(argv) > 1 else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = None
    if isinstance(payload, dict) and not event:
        event = payload.get("hook_event_name") or payload.get("agent_action_name") or ""
    try:
        stdout, request = handle(
            ide,
            event,
            payload,
            now_ns=time.time_ns(),
            state_dir=Path(os.environ.get("OTERU_HOOK_STATE") or Path.home() / ".cache" / "oteru-hooks"),
            env=os.environ,
        )
    except Exception:  # noqa: BLE001
        stdout, request = neutral_stdout(ide, event), None
    if stdout:
        print(stdout, flush=True)
    if request:
        try:
            timeout = float(os.environ.get("OTERU_HOOK_TIMEOUT") or 1.0)
        except ValueError:
            timeout = 1.0
        send(request, timeout=timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
