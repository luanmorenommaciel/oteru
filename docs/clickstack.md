ClickStack (ClickHouse + HyperDX) integration
=============================================

How to land Oteru telemetry in a local
[ClickStack](https://clickhouse.com/use-cases/observability) — the ClickHouse
observability stack — using the `hyperdx-all-in-one` Docker image, with
persistent storage and automated API-key bootstrap.

What ClickStack is
------------------

The `docker.hyperdx.io/hyperdx/hyperdx-all-in-one` image bundles four
components in one container:

| Component | Role | Where it lives |
|---|---|---|
| **ClickHouse** | Columnar database holding all telemetry (`otel_logs`, `otel_traces`, `otel_metrics_*` tables) | `/var/lib/clickhouse` → volume `hyperdx-clickhouse` |
| **OTel collector (embedded)** | OTLP ingest on `:4317` (gRPC) / `:4318` (HTTP); **requires** an `authorization` header with the team's ingestion API key — even locally | in-container |
| **HyperDX UI/API** | Search, dashboards, alerts at http://localhost:8080 | in-container |
| **MongoDB** | App metadata only: accounts, teams, API keys, dashboards, saved searches — never telemetry | `/data/db` → volume `hyperdx-mongo` |

Two integration modes
---------------------

### Mode A — direct ingest (`make up-direct`) — simplest

The emitter sends OTLP straight into ClickStack; no intermediate collector.

```
oteru-emitter ── OTLP/HTTP :4318 + authorization header ──► ClickStack ──► ClickHouse
```

```bash
make up-direct     # start ClickStack alone + bootstrap the API key into .env
make ingest        # replay the full sample once (523 batches)
make ingest-loop   # keep ingesting forever (Ctrl+C to stop)
make down-direct   # stop (volumes are kept)
```

`up-direct` uses `oteru-collector/docker-compose.hyperdx-direct.yml`, which
publishes 8080 (UI), 4317 and 4318 on the host, then runs
`scripts/hyperdx_bootstrap.sh` (see below). `ingest` reads `HYPERDX_API_KEY`
from `oteru-collector/.env` and passes it with the emitter's `--header` flag:

```bash
oteru-emitter replay samples/telemetry-sample.json \
  --transport http --header "authorization=$HYPERDX_API_KEY"
```

### Mode B — through the oteru-collector (`make up-hyperdx`)

The sandbox collector stays the single entry point and fans out to `debug`
(stdout), `file` (`telemetry/telemetry.json`) **and** ClickStack. Use this when
you want the local capture file and stdout visibility in addition to the UI,
or when live Claude Code sessions and the emitter must share one endpoint.

```
oteru-emitter / Claude Code CLI
        │ OTLP :4317/:4318
        ▼
oteru-collector ──► debug (stdout)
                ├─► file  (telemetry/telemetry.json)
                └─► otlp_http + authorization header ──► ClickStack (internal Docker network)
```

```bash
make up-hyperdx    # collector + ClickStack; UI at http://localhost:8080
make demo          # send 5 batches through the collector
make down          # stop
```

Compose override: `docker-compose.hyperdx.yml` (ClickStack's OTLP ports stay
internal — only the collector reaches them, as service name `hyperdx`).
Collector config: `oteru-collector-config.hyperdx.yml` (adds the
`otlp_http/hyperdx` exporter, authenticated via `${env:HYPERDX_API_KEY}`).

> **The two modes are mutually exclusive at runtime** — both claim host ports
> 4317/4318. `make down` / `make down-direct` the other one first. They share
> the same named volumes, so data and accounts carry over between modes.

There is also `make up-clickstack` for forwarding to a **remote/cloud**
ClickStack (`CLICKSTACK_ENDPOINT` + `CLICKSTACK_API_KEY`) — same idea as
mode B with an external backend.

API-key bootstrap
-----------------

ClickStack's ingest endpoint returns **HTTP 401** without an `authorization`
header carrying the team's ingestion API key. The key only exists after a
team is created, which normally happens manually in the UI.
`scripts/hyperdx_bootstrap.sh` automates the whole handshake:

1. waits for the container to report `healthy`;
2. registers a local dev account via `POST /api/register/password`
   (first run only — later runs get `teamAlreadyExists`, which is fine);
3. reads the team's `apiKey` from HyperDX's internal MongoDB
   (tries `mongosh`, falls back to the legacy `mongo` shell — the image has
   shipped both, depending on version);
4. writes `HYPERDX_API_KEY=<key>` into `oteru-collector/.env` (gitignored).

Defaults (override with env vars before running):

| Variable | Default | Meaning |
|---|---|---|
| `HYPERDX_EMAIL` | `dev@oteru.local` | UI login (local sandbox only) |
| `HYPERDX_PASSWORD` | `oteru-Dev-Password1!` | UI password (local sandbox only) |
| `HYPERDX_CONTAINER` | `oteru-collector-hyperdx-1` | container to bootstrap |

The key never leaves `.env` (gitignored) and is passed at runtime only — via
the emitter's `--header` flag (mode A) or the collector's environment
(mode B). **Never commit it**, even though it only unlocks a local sandbox.

Persistence
-----------

Both compose files mount the same named volumes:

- `hyperdx-clickhouse` → `/var/lib/clickhouse` — all telemetry;
- `hyperdx-mongo` → `/data/db` — accounts, API keys, dashboards, saved
  searches.

Data therefore survives `docker compose down`, container recreates and
switching between modes A and B. Factory reset (destroys telemetry **and**
the account/API key — you'll need to bootstrap again):

```bash
cd oteru-collector && docker compose -f docker-compose.hyperdx-direct.yml down -v
```

Using the data
--------------

- **UI** — http://localhost:8080, log in with the bootstrap account. In
  *Search*, pick the **Logs** source, set the time range to *Last 15 minutes*
  (or *Live Tail*) and filter, e.g. `service.name:claude-code` or free-text
  `plugin_loaded`. Switch the source to **Metrics** for the `claude_code.*`
  counters. *Chart Explorer* / *Dashboards* plot metrics; saved searches can
  drive alerts.
- **SQL** — straight into ClickHouse:

  ```bash
  docker exec oteru-collector-hyperdx-1 clickhouse-client \
    --query "SELECT Timestamp, Body FROM default.otel_logs ORDER BY Timestamp DESC LIMIT 20"
  ```

Troubleshooting
---------------

| Symptom | Cause / fix |
|---|---|
| Collector logs `Exporting failed … 401 … missing or empty authorization header` | `HYPERDX_API_KEY` missing or stale in `oteru-collector/.env` — run `bash scripts/hyperdx_bootstrap.sh`, then recreate the collector (mode B) |
| Emitter says `ok` but the UI looks empty | Wrong source/time range in the UI — select the *Logs* source and *Last 15 minutes*; the demo bursts are tiny |
| `port is already allocated` on 4317/4318 | The other mode is still running — `make down` or `make down-direct` first |
| `mongosh: executable file not found` | Image version ships the legacy shell — use `mongo` (the bootstrap script tries both) |
| Everything gone after a recreate | Pre-volume containers stored Mongo data in the container layer; with `hyperdx-mongo`/`hyperdx-clickhouse` in place this no longer happens — unless `down -v` was used |
