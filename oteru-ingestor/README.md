# oteru-ingestor

OTLP → ClickHouse ingestor, written three times — **Python (#24), Go (#25),
Rust (#26)** — against one spec ([`SPEC.md`](SPEC.md)), one schema
([`schema.sql`](schema.sql)) and one black-box conformance suite
([`../scripts/check_ingestor.sh`](../scripts/check_ingestor.sh)). The point is
the comparison (#3): same contract, three languages, then a benchmark decides
whether any of them should replace the collector's contrib `clickhouse`
exporter.

| Implementation | Dir | Port | Database | Status |
|---|---|---|---|---|
| Python | [`python/`](python/) | 14318 | `ingest_python` | conformant (11/11), exporter-equivalent on the 523-batch sample |
| Go | [`go/`](go/) | 24318 | `ingest_go` | conformant (11/11), exporter-equivalent on the 523-batch sample |
| Rust | `rust/` | 34318 | `ingest_rust` | #26 |

## Run

Everything builds inside Docker — no local Python/Go/Rust toolchain needed.

```bash
make up-ingestor IMPL=python      # collector + ClickHouse + the ingestor on :14318
make ingestor-check IMPL=python   # conformance suite (needs `make setup` once)
make down-ingestor
```

Send it traffic like any OTLP/HTTP endpoint:

```bash
cd oteru-emitter && .venv/bin/oteru-emitter replay samples/telemetry-sample.json \
  --endpoint http://localhost:14318 --max-gap 0
```

and query `ingest_python.otel_logs` / `otel_traces` / `otel_metrics_sum` /
`otel_metrics_gauge` — same columns as `otel.*`, so queries and views port
unchanged.

## Adding an implementation

1. `<lang>/Dockerfile` with build context `oteru-ingestor/` (the shared
   `schema.sql` must be copied in), listening on `4318`, database
   `ingest_<lang>`.
2. A `ingestor-<lang>` service with `profiles: ["<lang>"]` in
   `oteru-collector/docker-compose.ingestor.yml`.
3. `make up-ingestor IMPL=<lang> && make ingestor-check IMPL=<lang>` until green.
   The suite is the definition of done — do not edit it to fit an
   implementation; if the spec is ambiguous, fix `SPEC.md` and the suite
   together, for all three.

## Python notes

- Stdlib `ThreadingHTTPServer` + `opentelemetry-proto` + `requests`, inserting
  through ClickHouse's HTTP interface with `FORMAT JSONEachRow`; timestamps as
  `seconds.nanoseconds` strings keep full DateTime64(9) precision.
- Doubles render like the Go exporter (`0.00214`, `3`, never `1e-05`) so
  attribute maps match `otel.*` byte for byte.
- Deliberately unoptimised (one INSERT per table per request, no batching
  across requests): it is the readable baseline, and batching is a benchmark
  variable for #3.

## Go notes

- `net/http` + the collector's own `pdata` module for decoding (OTLP/JSON hex
  IDs handled natively) and for value rendering (`pcommon.Value.AsString`) —
  the same code path the contrib exporter uses, so parity is by construction.
- Same insert strategy as Python (JSONEachRow, one INSERT per table per
  request); static binary on `distroless/static:nonroot`.
- `go.sum` is committed; regenerate inside Docker, no local Go needed:
  `docker run --rm -v "$PWD/oteru-ingestor/go":/src -w /src golang:1.26 go mod tidy`.
