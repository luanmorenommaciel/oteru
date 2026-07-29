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

## Choosing which signals to send (`--emit`)

By default the emitter replays **every signal the capture holds**. `--emit`
narrows that to a comma-separated list of `log`, `metric` and `trace`:

```powershell
oteru-emitter replay samples\telemetry-sample.json --emit log            # logs only
oteru-emitter replay samples\telemetry-sample.json --emit metric         # metrics only
oteru-emitter replay tests\fixtures\traces-capture.json --emit trace     # traces only
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
  For the trace signal, use `tests/fixtures/traces-capture.json` (synthetic,
  fictitious IDs).
- It **is applied before `--limit`**, so `--emit metric --limit 5` sends five
  *metric* batches rather than the metrics among the first five batches.

## Signals: what Claude Code actually emits

| Signal | Claude Code | In `samples/telemetry-sample.json` | Fixture for testing |
|---|---|---|---|
| `log` | yes (`claude_code.*` events) | 348 batches | `tests/fixtures/tiny-capture.json` |
| `metric` | yes (`claude_code.*` counters) | 175 batches | `tests/fixtures/tiny-capture.json` |
| `trace` | **opt-in beta** — off unless `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` + `OTEL_TRACES_EXPORTER=otlp` | none | `tests/fixtures/traces-capture.json` |

The sample predates the traces beta, so it has none. Logs and metrics carry
empty trace IDs regardless — those records correlate via `session.id` /
`prompt.id`, not spans.

`tests/fixtures/traces-capture.json` fills the gap so the trace path (collector
ingress → ClickHouse `otel_traces`) can be exercised at all. It is **synthetic**
but modelled on the documented span schema — `claude_code.interaction` (root) →
`claude_code.llm_request` / `claude_code.tool` → `claude_code.tool.execution`,
with the documented attributes (`model`, `gen_ai.system`, `input_tokens`,
`tool_name`, `tool_use_id`, ...) and placeholder identity. Two things in it are
**not** verified against a real capture: the instrumentation scope name
(`com.anthropic.claude_code.traces`) and the exact attribute value formats.
Replace it with a redacted real capture once someone runs the beta.

## Development (tests and lint)

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

`profiles/` is the extension seam: each emitter (`claude_code`, future
`codex`, `crewai`) declares its metadata. In replay it defines which IDs to
rotate; in the synthetic generators (Phase 2+) it will also declare the event
catalog, the attribute schema and the lifecycle state machine.

## Roadmap

- **Phase 1 (current):** faithful replay, dual-transport, realtime, restamp.
- **Phase 2:** stochastic synthetic generator (state machine + distributions
  fitted to captures + invariants like `cost = f(tokens, model)`), seedable.
- **Phase 3:** new profiles (Codex, CrewAI) incl. the `gen_ai.*` path + spans.
- **Phase 4:** AI-authored scenario catalog (offline → fixtures).
