-- Minimal stand-ins for the tables the contrib clickhouse exporter creates
-- (create_schema: true), with the column names and types the views read.
-- Used only by scripts/check_views_compat.sh to create every view on a bare
-- ClickHouse with default settings. Keep in step when a view reads a new column.
CREATE DATABASE IF NOT EXISTS otel;
CREATE TABLE IF NOT EXISTS otel.otel_traces
(
    Timestamp          DateTime64(9),
    TraceId            String,
    SpanId             String,
    ParentSpanId       String,
    SpanName           LowCardinality(String),
    SpanKind           LowCardinality(String),
    ServiceName        LowCardinality(String),
    ResourceAttributes Map(LowCardinality(String), String),
    SpanAttributes     Map(LowCardinality(String), String),
    Duration           UInt64,
    StatusCode         LowCardinality(String),
    StatusMessage      String
)
ENGINE = MergeTree
ORDER BY Timestamp;
