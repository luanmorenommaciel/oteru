# oteru

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

Prerequisites: Docker running, Python 3.10+.

### 1. Start the collector

```bash
cd oteru-collector
docker compose up -d
docker compose logs oteru-collector   # wait for "Everything is ready"
```

### 2. Install the emitter (once)

```powershell
cd oteru-emitter
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # bash: source .venv/Scripts/activate
pip install -e .
```

### 3. Replay a capture

```bash
# validate without sending (no collector / network deps needed)
oteru-emitter replay samples/telemetry-sample.json --dry-run

# send for real
oteru-emitter replay samples/telemetry-sample.json --transport http
oteru-emitter replay samples/telemetry-sample.json --transport grpc --limit 5 --max-gap 1
```

Watch the records arrive with `docker compose logs -f oteru-collector` (from
`oteru-collector/`).

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
