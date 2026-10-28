Emitter coverage — which AI agent tools Oteru can observe, and how
==================================================================

Research snapshot, **2026-10-06**: every row was checked against the tool's
own docs or source (commit / URL in the last column). Telemetry support moves
fast — re-check a row before relying on it, and update this file when you do.

How a tool reaches Oteru — four paths, cheapest first:

| Path | What it takes | Where it lives |
|---|---|---|
| **A. On the surface** | the tool emits OTLP with OTel GenAI semconv (`gen_ai.operation.name` = `invoke_agent` / `execute_tool` / `chat`); point it at the collector | nothing to build — the integration surface (#22, `docs/integration-surface.md`) |
| **B. Small mapping** | `gen_ai.*` keys, but its own operation values or span shapes | a mapping (future work) |
| **C. Profile** | native OTLP in its own vocabulary | an emitter profile (#41, `oteru-emitter` README "Emitter profiles") |
| **D. Bridge** | no OTLP, but hooks or an API | a bridge: `oteru-hooks/` (#90) for hooks; pollers for APIs (not built) |
| **—** | nothing user-routable | out of reach |

## Coding agents and IDEs

| Tool | Path | How to point it at Oteru | Notes | Source |
|---|---|---|---|---|
| Claude Code | C (`claude_code`) | `CLAUDE_CODE_ENABLE_TELEMETRY=1` + `OTEL_EXPORTER_OTLP_*`; spans need `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` | `claude_code.*` names | live captures, Claude Code 2.1.191/2.1.220 |
| OpenAI Codex CLI | C (`codex`) | `[otel]` in `config.toml` (off by default) | `codex.*` events; `service.name` = originator | openai/codex@19c4793 `codex-rs/otel/` |
| GitHub Copilot — VS Code, CLI, app, JetBrains | A (`copilot_chat` for replay) | `github.copilot.chat.otel.*` / `COPILOT_OTEL_ENABLED` + `OTEL_EXPORTER_OTLP_ENDPOINT` | identity only with `captureIdentity`; enterprise-managed settings exist | microsoft/vscode@9b2d899 `extensions/copilot/docs/monitoring/agent_monitoring.md`; docs.github.com/en/copilot/concepts/enterprise/opentelemetry |
| Cursor — Enterprise export | C (`cursor`) | Team Settings → OpenTelemetry Export (sent by Cursor's servers) | logs + delta metrics, no spans; OTLP/HTTP protobuf only | cursor.com/docs/enterprise/opentelemetry-export |
| Cursor — any plan | D (`oteru-hooks`) | `examples/cursor.hooks.json` | tool spans from `postToolUse` durations | cursor.com/docs/agent/hooks |
| Windsurf (Cascade) | D (`oteru-hooks`) | `examples/windsurf.hooks.json` | no native OTel; no exit code / call id in hooks | docs.devin.ai/desktop/cascade/hooks.md |
| Google Antigravity | D (`oteru-hooks`) | `examples/antigravity.hooks.json` | no native OTel; hooks reportedly CLI-only today; no tool durations | antigravity.google/docs/hooks |
| Gemini CLI | B | `GEMINI_TELEMETRY_ENABLED=true` + `GEMINI_TELEMETRY_OTLP_ENDPOINT` | `gen_ai.*` keys, own operation values (`llm_call`, `tool_call`) | google-gemini/gemini-cli@ef59c53 `docs/cli/telemetry.md` |
| Goose | A | `OTEL_EXPORTER_OTLP_ENDPOINT` | | block/goose@067ca1e `crates/goose/src/otel/` |
| Cline | C (no profile yet) | `CLINE_OTEL_TELEMETRY_ENABLED=true` + `CLINE_OTEL_EXPORTER_OTLP_ENDPOINT` | own events, metrics + logs | cline/cline@e5dd38d `otel-config.ts` |
| OpenHands | C? | `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (via Laminar SDK) | Laminar attributes, unverified shape | OpenHands software-agent-sdk@8ba966d |
| Kiro | — | enterprise admin console | daily aggregate usage metrics only, no runs | kiro.dev/docs/enterprise/monitor-and-track/user-activity/opentelemetry/ |
| Aider, Roo Code | — | | no OTel | Aider-AI/aider@5dc9490; RooCodeInc/Roo-Code@b867ec9 |
| Devin, Replit Agent, Amp, Jules | ? | | no public OTel docs found — unverified, not "no" | — |

An agent CLI running **inside** an editor (Claude Code or Codex in VS Code,
Windsurf, Sublime, a terminal) is observed through the CLI, not the editor —
its row above applies wherever it runs.

## Agent frameworks and runtimes

| Framework | Path | Enable | Source |
|---|---|---|---|
| Omnigent (Databricks) | A* (`omnigent` for replay) | `OMNIGENT_TELEMETRY_ENABLED=true` + `OTEL_EXPORTER_OTLP_ENDPOINT` | omnigent-ai/omnigent@fa1dbe6 `omnigent/inner/tracing.py` |
| CrewAI (native tracing) | A (`crewai`) | `telemetry_session(..., exporters=[otlp_exporter(...)])` | crewAIInc/crewAI@e836a191 `telemetry/tracing/` |
| Microsoft Agent Framework | A | `configure_otel_providers()` + `OTEL_EXPORTER_OTLP_*` | learn.microsoft.com/en-us/agent-framework/agents/observability |
| Semantic Kernel | A (bring your own SDK/exporter) | `SEMANTICKERNEL_EXPERIMENTAL_GENAI_ENABLE_OTEL_DIAGNOSTICS=true` | microsoft/semantic-kernel@64005c7 |
| AutoGen (maintenance) | A (bring your own SDK) | `tracer_provider=` on the runtime | microsoft/autogen python-v0.7.5 `_telemetry/_genai.py` |
| Azure AI Foundry (client-side SDK) | A | `Azure.Experimental.EnableGenAITracing` → OTLP | learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup |
| Google ADK | A | `OTEL_EXPORTER_OTLP_ENDPOINT` | google/adk-python@f03d23d `telemetry/` |
| AWS Strands Agents | A | `StrandsTelemetry().setup_otlp_exporter()` | strands-agents/sdk-python@79d8d07 |
| Pydantic AI | A | `Agent.instrument_all()` + OTLP SDK | pydantic/pydantic-ai@099b890 `docs/logfire.md` |
| Mastra | A | `@mastra/otel-exporter` | mastra-ai/mastra@49b9bc8d |
| LiteLLM | C (`litellm`) — partly A | `callbacks: ["otel"]` | BerriAI/litellm@d8bc2b78e4ab |
| LangChain / LangGraph | B | `LANGSMITH_TRACING=true` + `LANGSMITH_TRACING_MODE=otel` | langchain-ai/langsmith-sdk@2fd05b9 |
| Vercel AI SDK | B | `@ai-sdk/otel` | vercel/ai@be3985c |
| LlamaIndex, Haystack, smolagents | C (own / OpenInference) | official OTel integrations | run-llama/llama_index@cb4c917; deepset-ai/haystack@f2074da; huggingface/smolagents@96f33fa |
| Amazon Bedrock AgentCore | depends on the framework run on it | ADOT; `DISABLE_ADOT_OBSERVABILITY=true` for non-CloudWatch backends | docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html |
| OpenAI Agents SDK | — (own tracing to OpenAI) / D via `add_trace_processor()` | | openai/openai-agents-python@911f106 `docs/tracing.md` |

\* Omnigent is on the surface except one rule: failed spans carry no
`error.type` (asserted in `tests/test_cross_features.py` on the integration
branch). It also stamps no user identity.

## Microsoft 365 / Copilot Studio / consumer Copilot

| Product | Path | What exists | Source |
|---|---|---|---|
| Microsoft 365 Copilot (Word, Excel, Teams, Chat) | D (poller, not built) | Purview audit `CopilotInteraction` (RecordType 261: `AppHost`, `ThreadId`, `Messages[]`, `AccessedResources[]`, `ModelTransparencyDetails[]`, `AISystemPlugin[]` — IDs, no text); Graph `aiInteractionHistory/getAllEnterpriseInteractions` (beta, prompts + responses, `AiEnterpriseInteraction.Read.All`); usage reports (aggregates) | learn.microsoft.com/en-us/office/office-365-management-api/copilot-schema |
| Copilot Studio — environment-level telemetry (preview) | D (App Insights → Event Hub → collector, unverified) | `InvokeAgent` / `ExecuteTool` spans with `gen_ai.*`, but GUID trace/span IDs | learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-environment-level-agent-telemetry |
| Copilot Studio — agent-level | D (same route) | `customEvents` (`BotMessageReceived`, `TopicStart`, …), not OTel-aligned | learn.microsoft.com/en-us/microsoft-copilot-studio/telemetry-overview |
| GitHub Copilot cloud coding agent, Visual Studio | — | audit-log session events, usage metrics API | docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-enterprise/manage-agents/monitor-agentic-activity |
| Copilot in Windows / consumer | — | diagnostic data to Microsoft only | support.microsoft.com (Copilot privacy FAQ) |

## Next steps this suggests

1. **M365 Copilot poller** (Purview audit + Graph interaction export → OTLP
   `gen_ai.*`): the highest-value enterprise gap; needs a test tenant with
   Copilot licenses.
2. **Mappings for path B** (Gemini CLI, LangGraph, Vercel AI SDK): small,
   turns three popular tools into first-class citizens.
3. **Profiles for Cline / OpenHands** once a real capture confirms the shape.
