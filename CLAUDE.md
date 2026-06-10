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

## Cross-cutting gotchas

- **PII discipline.** `oteru-emitter/samples/telemetry-sample.json` is the only
  committed capture, with identity redacted. `oteru-collector/telemetry/*.json`
  is gitignored. Never commit un-redacted captures.
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
