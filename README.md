# oteru

[![CI](https://github.com/luanmorenommaciel/oteru/actions/workflows/ci.yml/badge.svg)](https://github.com/luanmorenommaciel/oteru/actions/workflows/ci.yml)

Agentic Observability Platform. One OTel spine for AI governance, tracing, and
autonomous intelligence.

This monorepo hosts the telemetry sandbox that exercises the Oteru pipeline
end-to-end without depending on live AI sessions:

| Subproject | Role | Docs |
|---|---|---|
| [`oteru-emitter/`](oteru-emitter/) | **Sends.** Forges OTLP telemetry traffic faithful to what Claude Code emits — replay (and, later, synthetic generation) of logs/metrics/traces. Python CLI. | [README](oteru-emitter/README.md) |
| [`oteru-collector/`](oteru-collector/) | **Receives.** OpenTelemetry Collector (contrib) sandbox via Docker Compose — OTLP ingress on gRPC `:4317` and HTTP `:4318`, output to stdout (`debug`) and to disk (`file`). | [README](oteru-collector/README.md) |

```
oteru-emitter / Claude Code CLI / future emitters
        │  OTLP (gRPC :4317 │ HTTP :4318)
        ▼
oteru-collector ──► debug (stdout)
                └─► file  (telemetry/telemetry.json)
```

## Quickstart

Prerequisites: Docker running, Python 3.10+, GNU make (on Windows, run `make`
from **Git Bash**).

```bash
make setup     # venv + editable install (dev extras) + PII pre-commit hook
make demo      # starts the collector, replays 5 batches over HTTP, tails the logs
```

If you are on macOS, the new helper script is:

```bash
./scripts/run_macos_integration.sh
```

Step by step, the same flow is:

```bash
make up        # collector via docker compose (detached)
make dry-run   # validate the sample without sending (no network needed)
cd oteru-emitter && .venv/Scripts/oteru-emitter replay samples/telemetry-sample.json --transport http
```

Watch the records arrive with `docker compose logs -f oteru-collector` (from
`oteru-collector/`). `make down` stops the collector. Run `make` with no
arguments to list every target.

To land everything in a **local ClickStack** (ClickHouse + HyperDX) with a
searchable UI at http://localhost:8080, use `make up-direct` + `make ingest`
(direct, simplest) or `make up-hyperdx` (through the collector) — see
[`docs/clickstack.md`](docs/clickstack.md). For a remote/cloud ClickStack, set
`CLICKSTACK_ENDPOINT` + `CLICKSTACK_API_KEY` and use `make up-clickstack` —
see [`oteru-collector/README.md`](oteru-collector/README.md). Credentials go
in env vars or the gitignored `.env`, **never in the repo**.

## Development

```bash
make test       # pytest suite (oteru-emitter/tests)
make lint       # ruff check + format check
make format     # apply ruff format + autofixes
make pii-guard  # scan committed captures for real identity (system python)
```

CI (GitHub Actions) runs lint + tests on Ubuntu and Windows × Python 3.10 and
3.13, plus the PII guard, for any change touching `oteru-emitter/`,
`scripts/` or the workflow itself.

### PII guard

The #1 documented risk here is committing an un-redacted capture.
`scripts/check_pii.py` (stdlib-only) scans `oteru-emitter/samples/` and
`oteru-emitter/tests/fixtures/` for real e-mails, user paths and non-placeholder
identity attributes. It runs as a **pre-commit hook** (enabled by `make setup`
via `git config core.hooksPath .githooks`), as `make pii-guard`, and as a CI
job.

## Roadmap

- **Phase 1 (current):** faithful replay of captured OTLP/JSON — dual-transport
  (HTTP/protobuf + gRPC), realtime pacing, restamped timestamps and rotated
  per-run correlation IDs.
- **Phase 2:** stochastic synthetic generator (lifecycle state machine +
  distributions fitted to captures + invariants like `cost = f(tokens, model)`),
  seedable.
- **Phase 3:** new emitter profiles (Codex, CrewAI) incl. `gen_ai.*` + spans.
- **Phase 4:** AI-authored scenario catalog (offline → fixtures).

## Notes

- `oteru-emitter/samples/telemetry-sample.json` is a committed fixture with PII
  redacted. Live captures (`oteru-collector/telemetry/*.json`) are gitignored —
  **never commit un-redacted captures**, they carry user identity.
- If a live Claude Code session has `CLAUDE_CODE_ENABLE_TELEMETRY=1` pointed at
  the same collector, real and synthetic `claude_code.*` traffic mix and are
  indistinguishable at the OTLP envelope — see
  [`oteru-collector/CLAUDE.md`](oteru-collector/CLAUDE.md) for the details.
