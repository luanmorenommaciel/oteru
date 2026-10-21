Minimum Integration Surface (OTLP + MCP)
========================================

**Status: proposal v0** (#22). Pending the claim-vs-design arbitration in
ADR-001 (#7); this document is the "design via a minimum surface" position
written down so it can be judged concretely. Until #7 is decided, treat the
rules as the target, not as a gate.

What it is
----------

The surface is the smallest contract an agent has to meet for Oteru to
observe it, **whatever its stack** — framework, language, model vendor. It
replaces per-framework adapters (Silmara's framing, #22): instead of Oteru
learning every framework, every agent speaks two open standards Oteru already
understands.

1. **Transport: OTLP.** gRPC on `:4317` or HTTP/protobuf on `:4318` — both
   land in the same pipelines (verified by `make e2e-signals`, #40).
2. **Vocabulary: OpenTelemetry GenAI + MCP semantic conventions.** Attribute
   names below come from
   [open-telemetry/semantic-conventions-genai](https://github.com/open-telemetry/semantic-conventions-genai)
   (read at commit `4f85037`, 2026-10-06). Those conventions are still
   *development* stability upstream — expect renames, and update this table
   and `oteru_emitter/surface.py` together when they happen.

On the surface vs outside
-------------------------

A span is **on the surface** when it says what it is:

- `gen_ai.operation.name` is set (a GenAI operation), or
- `mcp.method.name` is set (an MCP call).

Every other span is **outside**: not invalid, just not observable through the
contract. Claude Code's native spans (`claude_code.interaction`,
`claude_code.llm_request`, …) are outside today — they carry
`gen_ai.request.model` but no `gen_ai.operation.name`. Bringing such an
emitter onto the surface is a profile's job (#41), not a rule exception.

Rules
-----

`error` = the contract is broken; `warning` = recommended upstream and needed
by an Oteru feature, but absent.

| Span | Attribute | Level | Why Oteru needs it |
|---|---|---|---|
| *(resource)* | `service.name` | error | which agent/service — every grouping starts here |
| inference (`chat`, `text_completion`, `generate_content`, `embeddings`) | `gen_ai.provider.name` | error | vendor split; required upstream |
| inference | `gen_ai.request.model` | warning | model mix (#47); required upstream "if available" |
| inference | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | warning | cost (#45–#49) |
| `execute_tool` | `gen_ai.tool.name` | error | which tool ran; required upstream |
| `invoke_agent`, `create_agent` | `gen_ai.agent.name` | warning | per-agent activity (#50) |
| MCP `tools/call` | `gen_ai.tool.name` | error | joins MCP calls to tool executions (#42) |
| MCP `tools/call` | `gen_ai.operation.name = execute_tool` | warning | lets MCP tool calls be counted with every other tool call |
| MCP `resources/read`, `resources/subscribe`, `resources/unsubscribe` | `mcp.resource.uri` | error | which resource was fetched (#42) |
| MCP `prompts/get` | `gen_ai.prompt.name` | error | which prompt was fetched |
| MCP request (any method except `notifications/*`) | `jsonrpc.request.id` | warning | correlates request and response |
| any span on the surface with status `ERROR` | `error.type` | error | failure analysis, anomaly baselines (#64) |

Recommended but **not checked** (no Oteru feature depends on them yet):
`mcp.session.id`, `mcp.protocol.version`, `gen_ai.conversation.id`,
`gen_ai.response.finish_reasons`, `server.address`. Content attributes
(`gen_ai.input.messages`, `gen_ai.tool.call.arguments`, …) are **opt-in**
upstream and carry user data — they fall under the PII strategy (ADR-003, #9),
not under this surface.

Span names follow upstream (`{gen_ai.operation.name} {gen_ai.request.model}`,
`execute_tool {gen_ai.tool.name}`, `{mcp.method.name} {target}`) but are not
checked: queries key on attributes, never on names.

Checking a capture
------------------

```bash
cd oteru-emitter
oteru-emitter check my-capture.json            # exit 1 on errors, or if no span is on the surface
oteru-emitter check my-capture.json --strict   # warnings fail too
```

The capture is the collector's `file` exporter output
(`oteru-collector/telemetry/telemetry.json`) or anything `forge` / `replay`
reads. To produce a conforming example by hand, see `forge` (#43) and its
sample spec.

Scope of v0
-----------

- **Traces only.** Logs and metrics keep flowing (OTLP accepts them), but the
  surface does not judge them yet; `gen_ai.client.token.usage` and the MCP
  duration histograms are the natural next rows.
- **The checker is offline.** It reads a capture; it does not reject traffic
  at the collector. Enforcing at ingest is a decision for ADR-001 (#7).
