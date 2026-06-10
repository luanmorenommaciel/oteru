# oteru-collector

**OpenTelemetry Collector** sandbox (contrib distribution) for the Oteru
platform. Receives OTLP telemetry, prints it to stdout (`debug`) and persists
it to disk (`file`). It is the **receiving** end — its pair is the
[`oteru-emitter`](../oteru-emitter), which forges the traffic.

```
emitter / Claude Code / POD-1   ──OTLP──►   this collector   ──►  debug (stdout)
        (gRPC :4317 | HTTP :4318)                            └─►  file  (telemetry/)
```

## Prerequisites

- **Docker** running.

## Commands

```bash
docker compose up                       # foreground (telemetry prints here)
docker compose up -d                    # background
docker compose logs -f oteru-collector  # follow the output
docker compose down                     # stop
```

From the monorepo root, `make up` / `make down` / `make demo` (start + send 5
emitter batches + show the logs) do the same via the Makefile.

After editing `oteru-collector-config.yml`, recreate to remount the config:

```bash
docker compose up -d --force-recreate
```

## Ports (OTLP ingress)

| Port | Protocol | Use |
|---|---|---|
| `4317` | OTLP/gRPC | emitter `--transport grpc`, production POD-1 |
| `4318` | OTLP/HTTP (`http/protobuf` and `http/json`) | Claude Code CLI, emitter `--transport http` |

Both receivers feed the same pipelines (`traces`, `logs`, `metrics`).

## Exporters

All three signals fan out to **two** exporters (see
`oteru-collector-config.yml`):

- **`debug`** (`verbosity: detailed`) — prints human-readable telemetry to
  stdout.
- **`file`** (`/telemetry/telemetry.json`, with rotation) — writes structured
  OTLP/JSON, **one batch per line**. This is the format consumable by
  `jq`/Python and what `oteru-emitter` replays.

`telemetry/` is bind-mounted to the host (`docker-compose.yml`); the `*.json`
content is gitignored (data is not versioned).

## Capturing telemetry

The `telemetry/` folder automatically receives the `file` exporter output.
To follow it:

```bash
tail -f telemetry/telemetry.json                       # Git Bash
Get-Content telemetry\telemetry.json -Wait -Tail 20    # PowerShell
```

Manual pipeline sanity test (HTTP):

```bash
curl.exe -v -X POST http://localhost:4318/v1/logs \
  -H "Content-Type: application/json" \
  -d '{"resourceLogs":[{"scopeLogs":[{"logRecords":[{"body":{"stringValue":"manual test"}}]}]}]}'
```

`200 OK` + a `LogRecord` with `Body: Str(manual test)` in the log = healthy
pipeline.

## Emitting from the Claude Code CLI (hello world, `claude_code.*` namespace)

In another terminal, point Claude Code at this collector (HTTP):

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_METRIC_EXPORT_INTERVAL=10000
export OTEL_LOGS_EXPORT_INTERVAL=2000
claude
```

Claude Code emits **logs + metrics** (`claude_code.*`), no traces. To generate
traffic without a real session, use the [`oteru-emitter`](../oteru-emitter).
