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

## Storing in ClickHouse (self-contained, no external backend)

The override `docker-compose.clickhouse.yml` + config
`oteru-collector-config.clickhouse.yml` run a **bundled ClickHouse** on the
compose network and write to it with the contrib-native `clickhouse` exporter —
no HyperDX, no API key, no second collector hop. The exporter creates the `otel`
database and the `otel_logs` / `otel_traces` / `otel_metrics_*` tables on
startup (`create_schema: true`).

```bash
make up-clickhouse                        # collector + ClickHouse (from the monorepo root)
# send a capture through the emitter:
cd ../oteru-emitter && .venv/bin/oteru-emitter replay samples/telemetry-sample.json --transport http
# verify it landed (default creds otel/otel; HTTP interface on :8123):
curl -s 'http://localhost:8123/?user=otel&password=otel' --data-binary \
  "SELECT count() FROM otel.otel_logs"
make down-clickhouse                      # stop + remove the ClickHouse volume
```

Notes:

- The bundled ClickHouse is a **local dev backend** with throwaway credentials
  (`otel`/`otel`) — fine for the sandbox, not for anything shared. Recent
  ClickHouse requires a password for the `default` user over the network, so a
  dedicated `otel` user is provisioned via the image's env vars.
- Query the data on the host at `http://localhost:8123` (HTTP) or `:9000`
  (native), user `otel`, password `otel`.
- Choose your backend: **`up-clickstack`** forwards to an *external* ClickStack
  (managed / HyperDX UI); **`up-clickhouse`** is fully self-contained (raw
  ClickHouse tables you query with SQL).

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

That gives you **logs + metrics** (`claude_code.*`). To generate traffic without
a real session, use the [`oteru-emitter`](../oteru-emitter).

### Traces (opt-in beta)

Spans are **off by default** and need two extra variables on top of the ones
above:

```bash
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
export OTEL_TRACES_EXPORTER=otlp
```

The span hierarchy is `claude_code.interaction` (one per user prompt) →
`claude_code.llm_request` and `claude_code.tool` → `claude_code.tool.execution`.
Prompt text, tool parameters and tool output are **redacted by default**;
`OTEL_LOG_USER_PROMPTS`, `OTEL_LOG_TOOL_DETAILS` and `OTEL_LOG_TOOL_CONTENT`
opt back in — leave them off unless you have a reason, they carry real content.

Reference: [Claude Code monitoring
docs](https://code.claude.com/docs/en/monitoring-usage).

For a longer-lived setup, add the resource attributes and (if the endpoint is
authenticated) the headers:

```bash
export OTEL_RESOURCE_ATTRIBUTES=service.name=claude-code,environment=prod
export OTEL_EXPORTER_OTLP_HEADERS=authorization=<api-key>   # never commit this
```

Credentials belong in env vars or the gitignored `.env` — see
[`.env.example`](.env.example).

## Signal tolerance (partial payloads)

The collector accepts **any combination of signals**, and needs no extra
configuration to do so: the OTLP receiver routes each signal to its own
pipeline, so a batch with only `resourceLogs` never touches the metrics or
traces pipelines. A body with no records at all is accepted too:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:4318/v1/logs \
  -H 'content-type: application/json' --data '{}'      # -> 200
```

To verify this end to end against ClickHouse — every `--emit` combination
landing in the right tables, with no collector error — run from the repo root:

```bash
make up-clickhouse
make e2e-signals
```
