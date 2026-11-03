# oteru-hooks

Brings agent IDEs **without native OpenTelemetry** into Oteru (#90). Google
Antigravity and Windsurf (Cascade) export no OTLP; Cursor's export is
Enterprise-only and runs on Cursor's servers. All three can run a command on
each agent action, passing a JSON payload on stdin — `oteru_hook.py` is that
command. It turns each **agent turn into one trace**, with spans that sit on
the integration surface (#22), so Oteru needs no per-IDE profile:

```
invoke_agent <ide>                 one agent turn   gen_ai.operation.name=invoke_agent
├── execute_tool <tool>            built-in tool    gen_ai.tool.name, gen_ai.tool.call.id
└── tools/call <tool>              MCP tool call    mcp.method.name=tools/call
```

One file, Python stdlib only — nothing to install but the file itself.

## Install

1. Copy `oteru_hook.py` somewhere stable (e.g. `~/.oteru/oteru_hook.py`).
2. Merge the IDE's snippet from [`examples/`](examples/) into its hooks file,
   replacing `/path/to/oteru-hooks/oteru_hook.py`:

| IDE | Snippet | Hooks file |
|---|---|---|
| Cursor | [`cursor.hooks.json`](examples/cursor.hooks.json) | `~/.cursor/hooks.json` or `<project>/.cursor/hooks.json` |
| Windsurf / Devin Desktop | [`windsurf.hooks.json`](examples/windsurf.hooks.json) | `~/.codeium/windsurf/hooks.json` or `<project>/.devin/hooks.json` |
| Antigravity | [`antigravity.hooks.json`](examples/antigravity.hooks.json) | `~/.gemini/config/hooks.json` or `<project>/.agents/hooks.json` |

3. Point it at a collector (default `http://localhost:4318`, i.e. `make up`
   from the repo root):

```bash
export OTERU_OTLP_ENDPOINT=http://localhost:4318   # or OTEL_EXPORTER_OTLP_ENDPOINT
export OTERU_USER_ID=your-handle                    # optional, see "Identity"
```

## What each IDE gives, and the limits that follow

| | Turn span | Tool spans | Duration of tools |
|---|---|---|---|
| **Cursor** | `generation_id`: `beforeSubmitPrompt` → `stop` (`status: error` → span error) | `postToolUse` / `postToolUseFailure` (`tool_use_id`, `failure_type` → `error.type`) | from Cursor's own `duration` — stateless |
| **Windsurf** | `execution_id`: `pre_user_prompt` → `post_cascade_response` | `pre_/post_run_command`, `_read_code`, `_write_code`, `_mcp_tool_use` (MCP → `tools/call`) | pre/post paired in order through local state |
| **Antigravity** | `invocationNum`: `PreInvocation` → `PostInvocation` | `PostToolUse` (`error` → `error.type=tool_error`) | **none** — tools are point-in-time |

Limits, all from the vendors' docs (2026-10-06):

- **Antigravity `PreToolUse` is never registered**: it has no neutral answer —
  every legal `decision` changes the agent's permissions. Hence no tool
  durations. Its payloads carry no event name, so the snippet passes it as an
  argument. Hooks are reported to fire only in the `agy` CLI today.
- **Windsurf** sends no exit code on `post_run_command` and no call id — pre
  and post are paired by (turn, tool, target), first-in first-out. Its MCP
  spans carry no `jsonrpc.request.id`, so `oteru-emitter check` warns on them.
- **Cursor** shell / MCP hooks carry no call id, so the bridge builds tool
  spans from `postToolUse`, which covers every tool. MCP calls arrive there as
  ordinary tool spans (their `tool_name` format is undocumented).
- Pre/post state lives in `~/.cache/oteru-hooks` (`OTERU_HOOK_STATE`); losing
  it only costs a span its duration.

## Never in the agent's way

- Always exits 0 and prints the neutral answer each IDE expects — Cursor
  **blocks** a permission hook that returns invalid JSON, so permission hooks
  get `{"permission":"allow"}` and `beforeSubmitPrompt` gets
  `{"continue":true}`; Antigravity's `PostInvocation` gets an empty
  `injectSteps`; Windsurf gets nothing (exit code only).
- Sends with a 1 s timeout (`OTERU_HOOK_TIMEOUT`), no retries; a down
  collector is silently skipped.

## Identity and content

- **Identity is opt-in.** None of the three payloads identifies the user
  reliably (Cursor's `user_email` can be null; the others have none), and the
  bridge never invents one: `OTERU_USER_ID` becomes the resource
  `enduser.id`, otherwise no identity is sent. Cursor's `user_email` is never
  forwarded.
- **Content is opt-in.** Prompts, tool arguments and tool outputs are dropped
  unless `OTERU_CAPTURE_CONTENT=1`, which adds `gen_ai.tool.call.arguments` /
  `gen_ai.tool.call.result` (PII strategy: ADR-003, #9).

Resource: `service.name` = `cursor-hooks` / `windsurf-hooks` /
`antigravity-hooks` (distinct from Cursor's native `cursor` export), plus
`oteru.bridge=hooks` and `oteru.bridge.version`.

## Develop

```bash
make test-hooks      # from the repo root: unit tests built from each vendor's documented payloads
```
