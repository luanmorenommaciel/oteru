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

## Local ClickStack (HyperDX all-in-one)

For a fully local ClickHouse + HyperDX backend with persistent volumes and
automated API-key bootstrap, see [`docs/clickstack.md`](../docs/clickstack.md):

```bash
make up-direct     # ClickStack alone; emitter ingests direct on :4318
make up-hyperdx    # collector + ClickStack (collector forwards internally)
```

## Forwarding to ClickStack (optional)

The alternative config `oteru-collector-config.clickstack.yml` keeps `debug` +
`file` and **adds** an `otlphttp/clickstack` exporter that forwards all three
signals to a [ClickStack](https://clickhouse.com/use-cases/observability)
(ClickHouse + HyperDX) ingest collector, authenticated by API key:

```bash
# from the monorepo root — needs both env vars (or oteru-collector/.env,
# copied from .env.example; .env is gitignored)
export CLICKSTACK_ENDPOINT=http://host.docker.internal:4318
export CLICKSTACK_API_KEY=<hyperdx-ingestion-api-key>
make up-clickstack
```

Notes:

- From inside the container, "localhost" on the host machine is
  `host.docker.internal` — use it in `CLICKSTACK_ENDPOINT` for a local
  ClickStack.
- **Never commit endpoint/key values** — they are credentials. Pass them via
  environment or the gitignored `.env` only.
- The plain `make up` / `docker compose up -d` flow is untouched; `make down`
  stops either variant.

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
