oteru-ingestor — one spec, three implementations
================================================

The ingestor receives OTLP and writes it into ClickHouse — the job the
collector's contrib `clickhouse` exporter does today. It exists for the
**POD-of-3** exercise (#3): one PRD, implemented in **Python (#24), Go (#25)
and Rust (#26)**, all held to the same black-box conformance suite and then
benchmarked against each other. It is an *alternative* to the exporter, not a
replacement decided in advance: the benchmark is how that gets decided.

```
emitter / collector ──OTLP/HTTP──► oteru-ingestor-<lang> ──INSERT──► ClickHouse  ingest_<lang>.*
```

Contract
--------

### Transport

| | |
|---|---|
| Protocol | OTLP/HTTP, `POST /v1/logs`, `/v1/traces`, `/v1/metrics` |
| Content types | `application/x-protobuf` and `application/json` (OTLP/JSON: hex trace/span IDs, int64 as strings) |
| gzip | `Content-Encoding: gzip` accepted |
| Port | `4318` inside the container; published as **14318** (python), **24318** (go), **34318** (rust) |
| Health | `GET /healthz` → `200 ok` once the schema is applied and ClickHouse answers |
| gRPC | out of scope for v0 — front it with the collector if needed |

### Responses (OTLP/HTTP spec)

| Situation | Status | Body |
|---|---|---|
| accepted (also: empty body / no records) | `200` | `Export<Signal>ServiceResponse`, same encoding as the request |
| some points of an unsupported metric type | `200` | `partialSuccess.rejectedDataPoints` = count, `errorMessage` names the types |
| undecodable body | `400` | plain-text reason |
| unsupported `Content-Type` | `415` | plain-text reason |
| unknown path / method | `404` / `405` | |
| ClickHouse down / insert failed | `503` | plain-text reason (OTLP clients retry 503) |

### Storage

- Apply [`schema.sql`](schema.sql) at startup with `{db}` = `ingest_python`,
  `ingest_go` or `ingest_rust` (statements split on a line holding only `;`).
- **One INSERT per table per request** (v0: no cross-request batching — that
  is a benchmark variable, not a correctness one).
- Config from env: `CLICKHOUSE_URL` (HTTP interface, default
  `http://clickhouse:8123`), `CLICKHOUSE_USER` / `CLICKHOUSE_PASSWORD`
  (default `otel`/`otel`), `INGEST_DB` (default per language), `PORT`
  (default `4318`).

### Mapping (must match what the contrib exporter writes into `otel.*`)

| Column | Value |
|---|---|
| `ServiceName` | resource attribute `service.name`, `""` if absent |
| `ResourceAttributes`, `SpanAttributes`, `LogAttributes`, `Attributes` | `Map(String,String)`: strings as-is; int → decimal (`820`); double → shortest round-trip decimal (`0.00214`); bool → `true`/`false`; array / kvlist / bytes → JSON (`["a",1]`, base64 for bytes) |
| `TraceId`, `SpanId`, `ParentSpanId` | lowercase hex; `""` when the ID is empty / all-zero bytes absent |
| Traces `Timestamp` | `startTimeUnixNano`; `Duration` = `end - start` in ns |
| `SpanKind` | `Unspecified`, `Internal`, `Server`, `Client`, `Producer`, `Consumer` |
| `StatusCode` | `Unset`, `Ok`, `Error`; `StatusMessage` = status message |
| Logs `Timestamp` | `timeUnixNano`, or `observedTimeUnixNano` when that is 0 |
| Logs `Body` | the body AnyValue rendered like an attribute value |
| Logs `EventName` | the record's `event_name` field, `""` if unset |
| Metrics | `sum` → `otel_metrics_sum` (`AggregationTemporality` 1=delta / 2=cumulative, `IsMonotonic`); `gauge` → `otel_metrics_gauge`; `Value` = `asInt` or `asDouble` as Float64; one row per data point |
| Unsupported metric types | `histogram`, `exponentialHistogram`, `summary` → not stored, counted in `partialSuccess.rejectedDataPoints` |

Conformance
-----------

`make ingestor-check IMPL=python|go|rust` (`scripts/check_ingestor.sh`) is the
whole definition of done. Against a running ingestor it asserts, isolated per
run by an `oteru.e2e.run_id` resource attribute:

1. health answers;
2. logs, metrics and traces replayed by `oteru-emitter` (protobuf) land with
   the expected row counts — the same deltas `make e2e-signals` asserts for the
   exporter;
3. span parent links and trace IDs survive (hex, joinable);
4. typed attributes are rendered as the table above says (int, double, bool,
   string);
5. an OTLP/JSON request is accepted and stored;
6. a gzip request is accepted and stored;
7. an empty body answers 200; garbage answers 400; a wrong content type 415;
8. a histogram metric is reported in `partialSuccess.rejectedDataPoints` and
   not stored.

An implementation is done when every check is green. The benchmark (#3) runs
only against implementations that are.
