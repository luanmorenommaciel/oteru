# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in the `oteru` monorepo.

## Layout

Two subprojects, each with its own CLAUDE.md — read the one for the directory
you are working in:

- **`oteru-emitter/`** — Python package (`oteru_emitter`) + CLI `oteru-emitter`.
  Forges OTLP telemetry traffic (Phase 1: faithful replay of captured
  OTLP/JSON over HTTP `:4318` or gRPC `:4317`). See
  [`oteru-emitter/CLAUDE.md`](oteru-emitter/CLAUDE.md).
- **`oteru-collector/`** — OpenTelemetry Collector (contrib) sandbox via Docker
  Compose. Receives OTLP, prints to stdout (`debug`) and persists to
  `telemetry/telemetry.json` (`file`). See
  [`oteru-collector/CLAUDE.md`](oteru-collector/CLAUDE.md).

The emitter sends to the collector; defaults (`localhost:4318`/`4317`) line up
with the collector's published ports, so no config is needed to wire them.

## Commands

The root `Makefile` is the entry point (GNU make + bash; on Windows use Git
Bash). `make` with no arguments lists all targets:

```bash
make setup      # venv + pip install -e "./oteru-emitter[dev]" + PII pre-commit hook
make test       # pytest (oteru-emitter/tests)
make lint       # ruff check + ruff format --check
make format     # ruff format + autofixes
make dry-run    # validates the sample without network (523 batches)
make pii-guard  # python scripts/check_pii.py (system python — works before setup)
make e2e-signals # asserts every --emit combination reaches ClickHouse (needs up-clickhouse)
make up/down    # collector via docker compose
make up-clickstack  # collector + forward to ClickStack (needs CLICKSTACK_ENDPOINT/API_KEY env)
make demo       # up + 5 batches over HTTP + collector logs
make clean      # removes build/test caches (keeps .venv)
```

CI (`.github/workflows/ci.yml`): lint + tests on ubuntu/windows × Python
3.10/3.13 + a `pii-guard` job, path-filtered to `oteru-emitter/**`,
`scripts/**` and the workflow itself.

## Cross-cutting gotchas

- **PII discipline.** `oteru-emitter/samples/telemetry-sample.json` is the only
  committed capture, with identity redacted. `oteru-collector/telemetry/*.json`
  is gitignored. Never commit un-redacted captures.
- **Credentials discipline.** ClickStack endpoint/API key (and any future
  secrets) live only in env vars or the gitignored `.env`
  (`oteru-collector/.env.example` has the placeholders). The PII guard does
  NOT scan for credentials — never commit them anywhere.
- **PII guard is automated.** `scripts/check_pii.py` (stdlib-only) scans
  `oteru-emitter/samples/` and `oteru-emitter/tests/fixtures/` for real e-mails,
  user paths and non-placeholder identity attributes. It runs as the
  `.githooks/pre-commit` hook (enabled by `make setup`), as `make pii-guard`,
  and as a CI job. New fixtures must stay clean under it.
- **Signals are independent, and selection is not generation.** The emitter's
  `--emit log,metric,trace` filters what a capture already holds; any
  combination is valid and none implies another. The collector ingests partial
  payloads by construction (per-signal pipelines) — no normalization layer
  needed. Claude Code emits logs + metrics by default and **spans only under an
  opt-in beta** (`CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` +
  `OTEL_TRACES_EXPORTER=otlp`). Captures are never committed — the trace path is
  exercised by an OTLP payload built in `oteru-emitter/tests/factories.py`.
- **Live + synthetic traffic coexist.** A live Claude Code session with
  `CLAUDE_CODE_ENABLE_TELEMETRY=1` and the emitter's replays land on the same
  collector and are indistinguishable at the OTLP envelope. A clean separation
  (opt-in `--mark-synthetic` resource attribute) is deferred — details in
  `oteru-collector/CLAUDE.md`.
- **History note.** These subprojects were consolidated from the standalone
  repos `viniciussena/otel-emitter` and `viniciussena/otel-collector`
  (June 2026), renamed `otel-*` → `oteru-*` (Python package `otel_emitter` →
  `oteru_emitter`, CLI `otel-emitter` → `oteru-emitter`). Pre-consolidation
  history lives in the original repos.
