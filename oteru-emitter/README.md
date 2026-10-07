# oteru-emitter

Forges **OTLP** telemetry traffic faithful to what **Claude Code** emits — a
*synthetic / replay traffic* generator to validate the observability pipeline
(collector → backend → contract) without needing a real CLI session.

> Subproject of the [`oteru`](../README.md) monorepo, paired with
> [`oteru-collector`](../oteru-collector) (sibling directory), which runs the
> target OTel Collector.

## What it does (Phase 1 — faithful replay)

Reads an OTLP/JSON capture (what the collector's `file` exporter writes — one
batch per line) and **re-sends it to the collector**:

- **Byte-faithful to the structure.** Rebuilds the OTLP protobuf message via
  `opentelemetry-proto`, preserving types, attribute ordering and even the
  `claude_code.*` redundancies (`cost_usd` + `cost_usd_micros`,
  `Str("2501")` etc.).
- **Dual-transport.** Same message, two channels to choose from:
  `http/protobuf` (`:4318`) or `gRPC` (`:4317`).
- **Signal selection.** `--emit log,metric,trace` replays only the chosen
  signals. The three are independent — any combination is valid and none
  implies another.
- **Realtime.** Honors the original cadence between events (with a cap for
  idle gaps).
- **Restamp.** Re-stamps timestamps (everything shifted to "now") and rotates
  per-run correlation IDs (`session.id`, `prompt.id`, `request_id`)
  consistently, so the capture can be re-sent N times without the backend
  deduplicating it. The **principal identity** (`user.email`,
  `organization.id`, ...) is preserved.

## Step by step (from scratch)

> Commands in **PowerShell** (Windows). On macOS/Linux (bash/zsh), replace
> `.\.venv\Scripts\Activate.ps1` with `source .venv/bin/activate`, and any
> `.venv\Scripts\` path with `.venv/bin/`.

### Prerequisites

- **Python 3.10+** (`python --version`)
- **Docker** running (for the target OTel Collector)
- An OTLP/JSON **capture** to replay. The repo ships one at
  `samples\telemetry-sample.json` (real, PII redacted). To use live data,
  point at the sibling collector's output (see step 3).

### 1. Start the OTel Collector (sibling directory)

The emitter sends to a collector. Start the one in
[`oteru-collector`](../oteru-collector):

```powershell
cd ..\oteru-collector
docker compose up -d
docker compose logs oteru-collector | Select-String "Everything is ready"
```

It should listen on `:4318` (HTTP) and `:4317` (gRPC). To watch what arrives:

```powershell
docker compose logs -f oteru-collector
```

### 2. Install the emitter (once)

```powershell
cd oteru-emitter
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

After `pip install -e .` with the venv active, the `oteru-emitter` command is
available. (Without activating the venv, use
`.\.venv\Scripts\python.exe -m oteru_emitter.cli` instead of `oteru-emitter`.)

### 3. Validate without sending (dry-run)

Checks parsing and restamp, and prints the capture summary. Does **not**
require a collector:

```powershell
oteru-emitter replay samples\telemetry-sample.json --dry-run
```

### 4. Send for real

```powershell
# HTTP/protobuf (port 4318) — one of the protocols Claude Code can use
oteru-emitter replay samples\telemetry-sample.json --transport http

# gRPC (port 4317)
oteru-emitter replay samples\telemetry-sample.json --transport grpc
```

Tip for a quick first test: `--limit 5 --max-gap 1` sends only 5 batches and
never waits more than 1s between them.

### 5. Check the reception

In the `docker compose logs -f` terminal (step 1) you will see the records
arriving — with timestamps re-stamped to "now" and a new `session.id` on every
run. Or, on demand:

```powershell
cd ..\oteru-collector
docker compose logs --since 60s oteru-collector | Select-String "Body: Str|session.id"
```

## Useful recipes

```powershell
# accelerate 4x (compresses the time between events)
oteru-emitter replay samples\telemetry-sample.json --transport http --speed 4

# literal replay: ORIGINAL timestamps and IDs, no restamp
oteru-emitter replay samples\telemetry-sample.json --no-restamp --transport http

# reproducible: same ID rotation every time (useful for pipeline testing)
oteru-emitter replay samples\telemetry-sample.json --transport http --seed 42

# send to another collector
oteru-emitter replay samples\telemetry-sample.json --transport grpc --endpoint other-host:4317

# send to an authenticated backend (e.g. ClickStack/HyperDX ingest) — never hardcode the key
oteru-emitter replay samples\telemetry-sample.json --transport http `
  --endpoint http://localhost:4318 --header "authorization=$env:CLICKSTACK_API_KEY" --limit 5
```

Main flags: `--transport http|grpc`, `--endpoint`, `--header NAME=VALUE`
(repeatable), `--emit`, `--profile`, `--speed`, `--max-gap`, `--limit N`,
`--seed`, `--no-restamp`, `--dry-run`. Full help:
`oteru-emitter replay --help`.

## Checking a capture against the integration surface (`check`)

```bash
oteru-emitter check my-capture.json            # exit 1 on errors / nothing on the surface
oteru-emitter check my-capture.json --strict   # warnings fail too
```

Lists, per span, the attributes the Minimum Integration Surface requires and
the capture lacks — rules and rationale in
[`../docs/integration-surface.md`](../docs/integration-surface.md). Spans with
neither `gen_ai.operation.name` nor `mcp.method.name` are counted as
*outside* the surface (Claude Code's native `claude_code.*` spans, for now).

## Choosing which signals to send (`--emit`)

By default the emitter replays **every signal the capture holds**. `--emit`
narrows that to a comma-separated list of `log`, `metric` and `trace`:

```powershell
oteru-emitter replay samples\telemetry-sample.json --emit log            # logs only
oteru-emitter replay samples\telemetry-sample.json --emit metric         # metrics only
oteru-emitter replay my-capture.json --emit trace                       # traces only
oteru-emitter replay samples\telemetry-sample.json --emit log,metric     # both
```

The names are **singular**, order does not matter (output is always reported as
`log,metric,trace`), and the flag is repeatable (`--emit log --emit metric` is
the same as `--emit log,metric`).

There is **no dependency between signals**: a trace does not require a log or a
metric. This mirrors OTLP, where the three are separate pipelines.

Two things `--emit` deliberately does *not* do:

- It **selects, never fabricates.** Asking for a signal the capture does not
  hold is an error (exit 1), not an empty send — `samples/telemetry-sample.json`
  is a real Claude Code capture, so it has logs and metrics but **no traces**.
  For the trace signal, point it at a capture of your own taken with the traces
  beta on (see below).
- It **is applied before `--limit`**, so `--emit metric --limit 5` sends five
  *metric* batches rather than the metrics among the first five batches.

## Forging a trace by hand (`forge`)

To get trace data before any agent is instrumented — a demo, a new dashboard,
a contract test — describe the run in a small JSON spec and let `forge` turn it
into an OTLP traces capture. `replay` then sends it like any other capture:

```bash
oteru-emitter forge samples/manual-trace.spec.json -o run.json --seed 1
oteru-emitter replay run.json --profile generic --transport grpc
```

The spec names spans and links them; everything OTLP-specific (trace/span IDs,
nanosecond timestamps, value types, enum codes) is derived:

```json
{
  "resource": {"service.name": "my-agent"},
  "traces": [
    {"spans": [
      {"id": "root", "name": "invoke_agent my-agent", "duration_ms": 1200},
      {"id": "llm", "name": "chat my-model", "parent": "root", "kind": "client",
       "start_ms": 15, "duration_ms": 800, "status": "ok",
       "attributes": {"gen_ai.request.model": "my-model", "gen_ai.usage.input_tokens": 1830}}
    ]}
  ]
}
```

| Field | Meaning |
|---|---|
| `resource` | resource attributes; `service.name` defaults to `oteru-manual` |
| `scope` | instrumentation scope; defaults to `{"name": "oteru.manual"}` |
| `traces[].spans[].id` | local name, unique per trace — used only by `parent` |
| `parent` | `id` of the parent span (any order); omit for the root |
| `start_ms` / `duration_ms` | offsets from the forge time (start defaults to 0) |
| `kind` | `internal` (default), `server`, `client`, `producer`, `consumer` |
| `status` / `status_message` | `unset` (default), `ok`, `error` |
| `attributes` | strings, numbers, booleans, or lists of those |

Each trace becomes one batch with its own trace ID, and `parent` becomes
`parentSpanId`. A broken spec (unknown parent, duplicate id, parent cycle,
negative duration, nested attribute…) exits 1 naming the offending span.
`samples/manual-trace.spec.json` is a two-trace example — an agent run with LLM
calls and an MCP tool call, then a failing tool call — using the OTel `gen_ai.*`
and `mcp.*` attribute names. Without `-o`, the capture goes to stdout.

## Signals: what Claude Code actually emits

| Signal | Claude Code | In `samples/telemetry-sample.json` |
|---|---|---|
| `log` | yes (`claude_code.*` events) | 348 batches |
| `metric` | yes (`claude_code.*` counters) | 175 batches |
| `trace` | **opt-in beta** — off unless `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` + `OTEL_TRACES_EXPORTER=otlp` | none |

The sample was captured with the traces beta off, so it has none — the Claude
Code version behind it already supported traces; the exporter just wasn't
enabled. Logs and metrics carry empty trace IDs unless the beta is on; without
it those records correlate via `session.id` / `prompt.id`, not spans.

To get a capture with traces, enable the beta (see
[`oteru-collector/README.md`](../oteru-collector/README.md#traces-opt-in-beta)),
work for a while, and point the emitter at the collector's
`telemetry/telemetry.json`. The capture policy: **new captures are not
committed** — nothing un-redacted or realistic lands in the repo. The existing
`samples/telemetry-sample.json` is a redacted historical capture kept for
`make dry-run` and the integration tests; new fixtures (e.g. traces) are built
synthetically in `tests/factories.py` instead of committed as JSON.

Install with the dev extras and run the suite:

```bash
pip install -e ".[dev]"      # pytest + ruff
pytest                       # suite in tests/ (tiny fixture + real sample)
ruff check . && ruff format --check .
```

Or, from the monorepo root: `make test` / `make lint` / `make format`
(GNU make + Git Bash on Windows).

- The suite uses two captures: `tests/fixtures/tiny-capture.json` (synthetic,
  tiny) and `samples/telemetry-sample.json` (real, PII redacted) in the
  integration tests.
- **Any new fixture must pass the PII guard**
  (`python ../scripts/check_pii.py`): only `example.com/org/net` e-mails,
  placeholder identity values, no user paths.
- The protobuf tests use `pytest.importorskip("opentelemetry.proto")`;
  `grpcio` is never required by the suite.

## Architecture

```
OTLP/JSON capture
   │  sources/replay.py     (loads batches + anchors timestamps + --emit selection)
   ▼
   │  rewrite/restamp.py    (shifts time + rotates IDs — preserves structure)
   ▼
   │  model/otlp.py         (dict -> OTLP protobuf message, neutral model)
   ▼
   │  scheduler/realtime.py (paces by the real deltas)
   ▼
   └► transport/            (otlp_http.py | otlp_grpc.py)  -> collector
```

`profiles/` is the extension seam: each emitter declares its metadata (see
below). In replay it defines which IDs to rotate; in the synthetic generators
(Phase 2+) it will also declare the event catalog, the attribute schema and the
lifecycle state machine.

## Emitter profiles (`--profile`)

A profile tells replay how one tool's telemetry is shaped: which `service.name`
it announces, which scopes to expect, which IDs are **per-run correlation**
(rotated on every replay so re-sends don't dedupe) and which are **principal
identity** (never touched). Every profile is read off the tool's own source or
docs — `Profile.source` records where and when.

| Profile | Tool | `service.name` | Signals | On the integration surface? |
|---|---|---|---|---|
| `claude_code` | Claude Code CLI | `claude-code` | logs, metrics, traces (beta) | no — `claude_code.*` names |
| `codex` | OpenAI Codex CLI (`[otel]` in `config.toml`, off by default) | `codex_cli_rs`, `codex_exec`, `codex_vscode`, `codex_desktop`, `codex_mcp_server`, `codex_sdk_ts` | logs (`codex.*` in `event.name`), metrics, traces | no — own namespace |
| `copilot_chat` | GitHub Copilot Chat in VS Code (`github.copilot.chat.otel.*`) and the Copilot CLI (`COPILOT_OTEL_ENABLED`) | `copilot-chat`, `github-copilot` | traces, metrics, events | **yes** — `gen_ai.*` |
| `cursor` | Cursor, Enterprise OTLP export (sent by Cursor's servers) | `cursor` | logs, delta metrics — **no spans** | no — `cursor.*` |
| `litellm` | LiteLLM SDK / proxy (`callbacks: ["otel"]`) | `litellm` | traces (+ opt-in metrics/events) | partly — `gen_ai.*`, but `gen_ai.operation.name` only with content capture on |
| `crewai` | CrewAI native tracing (`telemetry_session` + OTLP exporter) | `crewai` | traces | **yes** — `gen_ai.*` |
| `omnigent` | Omnigent agent meta-harness (`OMNIGENT_TELEMETRY_ENABLED=true`) | `omni-server`, `omni-runner`, `omni-harness`, `omni-host`, `omnigent` | traces, metrics, logs | **almost** — `gen_ai.*` spans, but failed spans carry no `error.type`; no user identity |
| `generic` | anything | — | — | — (literal replay, no attribute rotation) |

`--profile auto` picks the profile **per `service.name`** found in the
capture — the right choice for the collector's `telemetry.json`, which mixes
every tool pointed at it. A service no profile claims is replayed literally
(only trace/span IDs and timestamps rewritten) with a warning, never an error:

```bash
oteru-emitter replay ../oteru-collector/telemetry/telemetry.json --profile auto
```

### What about tools without a profile?

- **An agent CLI inside any editor** (Claude Code or Codex in VS Code,
  Windsurf, Sublime, a bare terminal): the CLI is the emitter, not the editor,
  so its profile already covers it. Claude Code records where it ran in
  `terminal.type`, Codex in `terminal.type` / `originator`.
- **Tools that follow the OTel GenAI conventions** need no profile to be
  observable — that is what the integration surface (#22) is for. A profile
  only adds correct ID rotation on replay.
- **Google Antigravity and Windsurf (Cascade)** have no native OTLP export as of
  2026-10: both expose **hooks** (Antigravity: `PreToolUse`, `PostToolUse`,
  `PreInvocation`, `PostInvocation`, `Stop`, keyed by `conversationId`;
  Windsurf: `pre_/post_run_command`, `pre_/post_mcp_tool_use`, …, keyed by
  `trajectory_id`), and Windsurf an Enterprise analytics API. Getting them into
  Oteru takes a hook → OTLP bridge; if the bridge emits `gen_ai.*` it lands on
  the surface without a profile. Neither hook payload carries user identity —
  the bridge has to add it.
- **VS Code's own product telemetry** (`telemetry.telemetryLevel`) only goes
  to Microsoft and cannot be routed to a collector; Copilot Chat's OTel export
  above is the observable part.
- **CrewAI's built-in telemetry** (`service.name` `crewAI-telemetry`) is
  hard-wired to CrewAI's endpoint and never reaches a user collector; only the
  native tracing above does.

Adding a tool = one `Profile(...)` in `profiles/base.py` with its source, plus a
fixture in `tests/factories.py::PROFILE_FIXTURES` — the registry tests then
enumerate it automatically (service-name uniqueness, rotate/preserve
disjointness across all profiles, every declared key present and rotated).

## Roadmap

- **Phase 1 (current):** faithful replay, dual-transport, realtime, restamp.
- **Phase 2:** stochastic synthetic generator (state machine + distributions
  fitted to captures + invariants like `cost = f(tokens, model)`), seedable.
- **Phase 3:** profiles for Codex, Copilot Chat, Cursor, LiteLLM, CrewAI (#41,
  replay) — next: synthetic generation per profile.
- **Phase 4:** AI-authored scenario catalog (offline → fixtures).
