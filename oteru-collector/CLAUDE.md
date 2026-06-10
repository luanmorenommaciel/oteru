# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this directory of the
`oteru` monorepo.

## Overview

`oteru-collector` runs a single OpenTelemetry Collector (contrib distribution)
via Docker Compose. It receives OTLP telemetry on **gRPC `:4317`** and **HTTP
`:4318`**, prints it to stdout via the `debug` exporter, and persists it to
`telemetry/telemetry.json` via the `file` exporter. No application code, no
backend, no database — it is the **receiving** end of the Oteru pipeline.

The companion subproject `oteru-emitter/` (sibling directory) forges OTLP traffic and
sends it here; Claude Code CLI can also emit directly over HTTP.

## Commands

```bash
docker compose up                       # foreground; telemetry prints to stdout
docker compose up -d                    # background
docker compose logs -f oteru-collector   # tail received telemetry
docker compose down                     # stop
docker compose up -d --force-recreate   # apply config edits (config is bind-mounted)
```

## Architecture notes

- **OTLP ingress accepts both gRPC and HTTP.** `oteru-collector-config.yml`
  enables `grpc` on `0.0.0.0:4317` and `http` on `0.0.0.0:4318` under
  `receivers.otlp.protocols`; both are published in `docker-compose.yml`. Both
  protocols feed the same `traces`/`logs`/`metrics` pipelines.
- **All three signals fan into two exporters: `debug` and `file`.** `debug`
  (`verbosity: detailed`) prints human-readable text to stdout; `file`
  (`/telemetry/telemetry.json`, with rotation) writes structured OTLP/JSON, one
  batch per line. To route elsewhere, add an exporter and reference it in the
  pipeline — don't drop `debug`/`file` unless you mean to lose that visibility.
- **The `file` exporter persists to the host.** `/telemetry` inside the
  container is bind-mounted to `./telemetry`; `telemetry/*.json` is gitignored.
  The format is the raw OTLP payload (`resourceLogs`/`resourceMetrics`) — this is
  exactly what `oteru-emitter` replays.
- **Config is bind-mounted, not baked in.** Edits to `oteru-collector-config.yml`
  take effect on container recreate without rebuilding.

## Emitters

| Emitter | Namespace | Signals | Transport |
|---|---|---|---|
| Claude Code CLI | `claude_code.*` | logs + metrics (no traces) | HTTP `:4318` (`http/protobuf`) |
| `oteru-emitter` (sibling directory) | replays whatever it's fed | logs/metrics/traces | HTTP `:4318` or gRPC `:4317` |
| POD-1 production emitter (future) | `gen_ai.*` + `mcp.*` | traces (spans) + metrics | gRPC `:4317` |

Claude Code emits `claude_code.*` logs/metrics with empty Trace IDs; records are
correlated via `session.id` + `prompt.id`, not spans. To set it up, see the
README. To generate traffic without a live session, use `oteru-emitter/`.

### Coexistence gotcha — live Claude Code + synthetic emitter mix here

If `CLAUDE_CODE_ENABLE_TELEMETRY=1` is set in the environment (the hello-world
setup), **your live Claude Code session emits real telemetry to this collector
at the same time** the `oteru-emitter` replays synthetic traffic. Both streams
are `claude_code.*` and the emitter replays the `Resource` block **byte-faithful**
(`service.name=claude-code`, same `host.*`/`os.*`/`service.version`), so at the
OTLP envelope level **synthetic and real traffic are indistinguishable**.

Current (fragile, accidental) discriminators:
- **Email**: live = the real account email; emitter's redacted sample = `user@example.com`.
- **session.id**: live = stable for the session; emitter = rotated per run.
- **timestamps**: emitter records are restamped to ~now in bursts.

Implication: anything that aggregates this data (metering, cost, contracts)
will blend real dev usage with test traffic. A clean separation is **deferred** —
the planned approach is an opt-in `--mark-synthetic` resource attribute on the
emitter (e.g. `oteru.traffic=synthetic`) that keeps the `claude_code.*` records
byte-identical while tagging the envelope. Until then, treat mixed captures as
contaminated and filter by the discriminators above. Note: `telemetry/*.json` is
gitignored, so this mix is never committed.
