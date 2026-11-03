-- oteru-ingestor schema — the ONE DDL every implementation (Python, Go, Rust)
-- applies at startup, with {db} replaced by its own database
-- (ingest_python / ingest_go / ingest_rust). See SPEC.md.
--
-- Column names and types mirror what the collector's contrib `clickhouse`
-- exporter writes into otel.* — minus its indexes, materialized k8s columns
-- and TTL — so every query and view written for otel.* runs unchanged against
-- an ingestor database, and the benchmark compares like with like.
-- Statements are separated by a line holding only `;` so implementations can
-- split without parsing SQL.

CREATE DATABASE IF NOT EXISTS {db}
;
CREATE TABLE IF NOT EXISTS {db}.otel_logs
(
    Timestamp           DateTime64(9),
    TraceId             String,
    SpanId              String,
    TraceFlags          UInt8,
    SeverityText        LowCardinality(String),
    SeverityNumber      UInt8,
    ServiceName         LowCardinality(String),
    Body                String,
    ResourceAttributes  Map(LowCardinality(String), String),
    ScopeName           String,
    ScopeVersion        LowCardinality(String),
    LogAttributes       Map(LowCardinality(String), String),
    EventName           String
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, Timestamp)
;
CREATE TABLE IF NOT EXISTS {db}.otel_traces
(
    Timestamp           DateTime64(9),
    TraceId             String,
    SpanId              String,
    ParentSpanId        String,
    TraceState          String,
    SpanName            LowCardinality(String),
    SpanKind            LowCardinality(String),
    ServiceName         LowCardinality(String),
    ResourceAttributes  Map(LowCardinality(String), String),
    ScopeName           String,
    ScopeVersion        String,
    SpanAttributes      Map(LowCardinality(String), String),
    Duration            UInt64,
    StatusCode          LowCardinality(String),
    StatusMessage       String
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SpanName, Timestamp)
;
CREATE TABLE IF NOT EXISTS {db}.otel_metrics_sum
(
    ResourceAttributes      Map(LowCardinality(String), String),
    ServiceName             LowCardinality(String),
    ScopeName               String,
    ScopeVersion            String,
    MetricName              String,
    MetricDescription       String,
    MetricUnit              String,
    Attributes              Map(LowCardinality(String), String),
    StartTimeUnix           DateTime64(9),
    TimeUnix                DateTime64(9),
    Value                   Float64,
    AggregationTemporality  Int32,
    IsMonotonic             Bool
)
ENGINE = MergeTree
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, TimeUnix)
;
CREATE TABLE IF NOT EXISTS {db}.otel_metrics_gauge
(
    ResourceAttributes      Map(LowCardinality(String), String),
    ServiceName             LowCardinality(String),
    ScopeName               String,
    ScopeVersion            String,
    MetricName              String,
    MetricDescription       String,
    MetricUnit              String,
    Attributes              Map(LowCardinality(String), String),
    StartTimeUnix           DateTime64(9),
    TimeUnix                DateTime64(9),
    Value                   Float64
)
ENGINE = MergeTree
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, TimeUnix)
