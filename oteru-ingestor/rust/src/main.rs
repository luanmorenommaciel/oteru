//! oteru-ingestor, Rust implementation (#26) — same contract as the Python
//! reference (../SPEC.md), held to the same black-box suite
//! (scripts/check_ingestor.sh).
//!
//! axum + tokio for HTTP, `opentelemetry-proto` (prost messages, serde for
//! OTLP/JSON) to decode, reqwest to ClickHouse's HTTP interface with
//! `FORMAT JSONEachRow` — one INSERT per table per request, like the other
//! two, so the #3 benchmark compares languages, not strategies.

use std::{
    io::Read,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use axum::{
    Router,
    body::Bytes,
    extract::{DefaultBodyLimit, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::Engine;
use opentelemetry_proto::tonic::{
    collector::{
        logs::v1::{ExportLogsServiceRequest, ExportLogsServiceResponse},
        metrics::v1::{
            ExportMetricsPartialSuccess, ExportMetricsServiceRequest, ExportMetricsServiceResponse,
        },
        trace::v1::{ExportTraceServiceRequest, ExportTraceServiceResponse},
    },
    common::v1::{AnyValue, KeyValue, any_value::Value as AV},
    metrics::v1::{NumberDataPoint, metric::Data, number_data_point::Value as NV},
};
use prost::Message;
use serde_json::{Map, Value as J, json};

const SPAN_KINDS: [&str; 6] = ["Unspecified", "Internal", "Server", "Client", "Producer", "Consumer"];
const STATUS_CODES: [&str; 3] = ["Unset", "Ok", "Error"];

struct App {
    ch_url: String,
    ch_user: String,
    ch_password: String,
    db: String,
    schema: String,
    http: reqwest::Client,
    ready: AtomicBool,
}

fn env(key: &str, default: &str) -> String {
    std::env::var(key).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

/// A request failure, mapped to its HTTP status.
enum Fail {
    Decode(String),     // 400
    MediaType(String),  // 415
    Ingest(String),     // 503 — OTLP clients retry
}

impl IntoResponse for Fail {
    fn into_response(self) -> Response {
        match self {
            Fail::Decode(m) => (StatusCode::BAD_REQUEST, format!("cannot decode body: {m}")),
            Fail::MediaType(m) => (StatusCode::UNSUPPORTED_MEDIA_TYPE, m),
            Fail::Ingest(m) => {
                eprintln!("{m}");
                (StatusCode::SERVICE_UNAVAILABLE, m)
            }
        }
        .into_response()
    }
}

// --- ClickHouse ---------------------------------------------------------------

impl App {
    async fn clickhouse(&self, query: &str, body: Option<String>) -> Result<(), Fail> {
        let req = match body {
            None => self.http.post(&self.ch_url).body(query.to_string()),
            Some(b) => self.http.post(&self.ch_url).query(&[("query", query)]).body(b),
        };
        let resp = req
            .basic_auth(&self.ch_user, Some(&self.ch_password))
            .send()
            .await
            .map_err(|e| Fail::Ingest(format!("clickhouse unreachable: {e}")))?;
        let status = resp.status();
        if status != reqwest::StatusCode::OK {
            let text = resp.text().await.unwrap_or_default();
            let text: String = text.trim().chars().take(300).collect();
            return Err(Fail::Ingest(format!("clickhouse {}: {text}", status.as_u16())));
        }
        Ok(())
    }

    async fn apply_schema(&self) -> Result<(), Fail> {
        for stmt in self.schema.replace("{db}", &self.db).split("\n;\n") {
            let sql: Vec<&str> = stmt.lines().filter(|l| !l.trim_start().starts_with("--")).collect();
            let sql = sql.join("\n");
            if !sql.trim().is_empty() {
                self.clickhouse(sql.trim(), None).await?;
            }
        }
        Ok(())
    }

    async fn insert(&self, table: &str, rows: Vec<J>) -> Result<(), Fail> {
        if rows.is_empty() {
            return Ok(());
        }
        let body = rows.iter().map(J::to_string).collect::<Vec<_>>().join("\n");
        self.clickhouse(&format!("INSERT INTO {}.{table} FORMAT JSONEachRow", self.db), Some(body)).await
    }
}

// --- value rendering (SPEC.md, "Mapping") -------------------------------------

fn json_value(v: &AnyValue) -> J {
    match &v.value {
        Some(AV::StringValue(s)) => J::String(s.clone()),
        Some(AV::BoolValue(b)) => J::Bool(*b),
        Some(AV::IntValue(i)) => json!(i),
        Some(AV::DoubleValue(d)) => json!(d),
        Some(AV::BytesValue(b)) => J::String(base64::engine::general_purpose::STANDARD.encode(b)),
        Some(AV::ArrayValue(a)) => J::Array(a.values.iter().map(json_value).collect()),
        Some(AV::KvlistValue(kv)) => J::Object(
            kv.values
                .iter()
                .map(|e| (e.key.clone(), e.value.as_ref().map(json_value).unwrap_or(J::Null)))
                .collect(),
        ),
        _ => J::Null,
    }
}

/// Rust's f64 Display already matches Go's FormatFloat(f, 'f', -1, 64):
/// shortest round-trip digits, never an exponent, `3` not `3.0`.
fn render(v: Option<&AnyValue>) -> String {
    let Some(v) = v else { return String::new() };
    match &v.value {
        Some(AV::StringValue(s)) => s.clone(),
        Some(AV::BoolValue(b)) => b.to_string(),
        Some(AV::IntValue(i)) => i.to_string(),
        Some(AV::DoubleValue(d)) => d.to_string(),
        None => String::new(),
        _ => json_value(v).to_string(),
    }
}

fn attrs(kvs: &[KeyValue]) -> Map<String, J> {
    kvs.iter().map(|kv| (kv.key.clone(), J::String(render(kv.value.as_ref())))).collect()
}

fn hexid(raw: &[u8]) -> String {
    if raw.iter().all(|b| *b == 0) { String::new() } else { hex::encode(raw) }
}

fn ts(ns: u64) -> String {
    format!("{}.{:09}", ns / 1_000_000_000, ns % 1_000_000_000)
}

fn service(res: &Map<String, J>) -> J {
    res.get("service.name").cloned().unwrap_or_else(|| J::String(String::new()))
}

// --- signal -> rows -------------------------------------------------------------

fn log_rows(req: &ExportLogsServiceRequest) -> Vec<J> {
    let mut rows = Vec::new();
    for rl in &req.resource_logs {
        let res = attrs(rl.resource.as_ref().map(|r| r.attributes.as_slice()).unwrap_or_default());
        for sl in &rl.scope_logs {
            let (scope_name, scope_version) =
                sl.scope.as_ref().map(|s| (s.name.as_str(), s.version.as_str())).unwrap_or_default();
            for r in &sl.log_records {
                let t = if r.time_unix_nano != 0 { r.time_unix_nano } else { r.observed_time_unix_nano };
                rows.push(json!({
                    "Timestamp": ts(t),
                    "TraceId": hexid(&r.trace_id),
                    "SpanId": hexid(&r.span_id),
                    "TraceFlags": r.flags & 0xFF,
                    "SeverityText": r.severity_text,
                    "SeverityNumber": r.severity_number,
                    "ServiceName": service(&res),
                    "Body": render(r.body.as_ref()),
                    "ResourceAttributes": res,
                    "ScopeName": scope_name,
                    "ScopeVersion": scope_version,
                    "LogAttributes": attrs(&r.attributes),
                    "EventName": r.event_name,
                }));
            }
        }
    }
    rows
}

fn span_rows(req: &ExportTraceServiceRequest) -> Vec<J> {
    let mut rows = Vec::new();
    for rs in &req.resource_spans {
        let res = attrs(rs.resource.as_ref().map(|r| r.attributes.as_slice()).unwrap_or_default());
        for ss in &rs.scope_spans {
            let (scope_name, scope_version) =
                ss.scope.as_ref().map(|s| (s.name.as_str(), s.version.as_str())).unwrap_or_default();
            for sp in &ss.spans {
                let (code, message) = sp
                    .status
                    .as_ref()
                    .map(|s| (s.code, s.message.as_str()))
                    .unwrap_or((0, ""));
                rows.push(json!({
                    "Timestamp": ts(sp.start_time_unix_nano),
                    "TraceId": hexid(&sp.trace_id),
                    "SpanId": hexid(&sp.span_id),
                    "ParentSpanId": hexid(&sp.parent_span_id),
                    "TraceState": sp.trace_state,
                    "SpanName": sp.name,
                    "SpanKind": SPAN_KINDS.get(sp.kind as usize).copied().unwrap_or("Unspecified"),
                    "ServiceName": service(&res),
                    "ResourceAttributes": res,
                    "ScopeName": scope_name,
                    "ScopeVersion": scope_version,
                    "SpanAttributes": attrs(&sp.attributes),
                    "Duration": sp.end_time_unix_nano.saturating_sub(sp.start_time_unix_nano),
                    "StatusCode": STATUS_CODES.get(code as usize).copied().unwrap_or("Unset"),
                    "StatusMessage": message,
                }));
            }
        }
    }
    rows
}

struct MetricRows {
    sums: Vec<J>,
    gauges: Vec<J>,
    rejected: i64,
    rejected_types: Vec<&'static str>,
}

fn metric_rows(req: &ExportMetricsServiceRequest) -> MetricRows {
    let mut out = MetricRows { sums: vec![], gauges: vec![], rejected: 0, rejected_types: vec![] };
    let reject = |n: usize, kind: &'static str, out: &mut MetricRows| {
        out.rejected += n as i64;
        if !out.rejected_types.contains(&kind) {
            out.rejected_types.push(kind);
        }
    };
    for rm in &req.resource_metrics {
        let res = attrs(rm.resource.as_ref().map(|r| r.attributes.as_slice()).unwrap_or_default());
        for sm in &rm.scope_metrics {
            let (scope_name, scope_version) =
                sm.scope.as_ref().map(|s| (s.name.as_str(), s.version.as_str())).unwrap_or_default();
            for m in &sm.metrics {
                let row = |p: &NumberDataPoint| {
                    let value = match p.value {
                        Some(NV::AsInt(i)) => i as f64,
                        Some(NV::AsDouble(d)) => d,
                        None => 0.0,
                    };
                    json!({
                        "ResourceAttributes": res,
                        "ServiceName": service(&res),
                        "ScopeName": scope_name,
                        "ScopeVersion": scope_version,
                        "MetricName": m.name,
                        "MetricDescription": m.description,
                        "MetricUnit": m.unit,
                        "Attributes": attrs(&p.attributes),
                        "StartTimeUnix": ts(p.start_time_unix_nano),
                        "TimeUnix": ts(p.time_unix_nano),
                        "Value": value,
                    })
                };
                match &m.data {
                    Some(Data::Sum(s)) => {
                        for p in &s.data_points {
                            let mut r = row(p);
                            r["AggregationTemporality"] = json!(s.aggregation_temporality);
                            r["IsMonotonic"] = json!(s.is_monotonic);
                            out.sums.push(r);
                        }
                    }
                    Some(Data::Gauge(g)) => out.gauges.extend(g.data_points.iter().map(row)),
                    Some(Data::Histogram(h)) => reject(h.data_points.len(), "histogram", &mut out),
                    Some(Data::ExponentialHistogram(h)) => {
                        reject(h.data_points.len(), "exponentialHistogram", &mut out)
                    }
                    Some(Data::Summary(s)) => reject(s.data_points.len(), "summary", &mut out),
                    None => {}
                }
            }
        }
    }
    out.rejected_types.sort_unstable();
    out
}

// --- HTTP -------------------------------------------------------------------------

/// Validates content type / encoding; returns (is_json, decompressed body).
fn prepare(app: &App, headers: &HeaderMap, body: Bytes) -> Result<(bool, Vec<u8>), Fail> {
    if !app.ready.load(Ordering::Acquire) {
        return Err(Fail::Ingest("starting".into()));
    }
    let ctype = headers
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .split(';')
        .next()
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    let is_json = match ctype.as_str() {
        "application/json" => true,
        "application/x-protobuf" => false,
        other => return Err(Fail::MediaType(format!("unsupported content type {other:?}"))),
    };
    let gzip = headers
        .get(header::CONTENT_ENCODING)
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v.eq_ignore_ascii_case("gzip"));
    let raw = if gzip {
        let mut out = Vec::new();
        flate2::read::GzDecoder::new(&body[..])
            .read_to_end(&mut out)
            .map_err(|e| Fail::Decode(e.to_string()))?;
        out
    } else {
        body.to_vec()
    };
    Ok((is_json, raw))
}

fn decode<T: Message + Default + serde::de::DeserializeOwned>(is_json: bool, raw: &[u8]) -> Result<T, Fail> {
    if raw.iter().all(u8::is_ascii_whitespace) {
        return Ok(T::default());
    }
    if is_json {
        serde_json::from_slice(raw).map_err(|e| Fail::Decode(e.to_string()))
    } else {
        T::decode(raw).map_err(|e| Fail::Decode(e.to_string()))
    }
}

fn respond<T: Message + serde::Serialize>(is_json: bool, resp: &T) -> Response {
    if is_json {
        let body = serde_json::to_vec(resp).unwrap_or_else(|_| b"{}".to_vec());
        ([(header::CONTENT_TYPE, "application/json")], body).into_response()
    } else {
        ([(header::CONTENT_TYPE, "application/x-protobuf")], resp.encode_to_vec()).into_response()
    }
}

async fn logs(State(app): State<Arc<App>>, headers: HeaderMap, body: Bytes) -> Result<Response, Fail> {
    let (is_json, raw) = prepare(&app, &headers, body)?;
    let req: ExportLogsServiceRequest = decode(is_json, &raw)?;
    app.insert("otel_logs", log_rows(&req)).await?;
    Ok(respond(is_json, &ExportLogsServiceResponse::default()))
}

async fn traces(State(app): State<Arc<App>>, headers: HeaderMap, body: Bytes) -> Result<Response, Fail> {
    let (is_json, raw) = prepare(&app, &headers, body)?;
    let req: ExportTraceServiceRequest = decode(is_json, &raw)?;
    app.insert("otel_traces", span_rows(&req)).await?;
    Ok(respond(is_json, &ExportTraceServiceResponse::default()))
}

async fn metrics(State(app): State<Arc<App>>, headers: HeaderMap, body: Bytes) -> Result<Response, Fail> {
    let (is_json, raw) = prepare(&app, &headers, body)?;
    let req: ExportMetricsServiceRequest = decode(is_json, &raw)?;
    let rows = metric_rows(&req);
    app.insert("otel_metrics_sum", rows.sums).await?;
    app.insert("otel_metrics_gauge", rows.gauges).await?;
    let mut resp = ExportMetricsServiceResponse::default();
    if rows.rejected > 0 {
        resp.partial_success = Some(ExportMetricsPartialSuccess {
            rejected_data_points: rows.rejected,
            error_message: format!("unsupported metric type(s): {}", rows.rejected_types.join(", ")),
        });
    }
    Ok(respond(is_json, &resp))
}

async fn healthz(State(app): State<Arc<App>>) -> Response {
    if app.ready.load(Ordering::Acquire) {
        "ok".into_response()
    } else {
        (StatusCode::SERVICE_UNAVAILABLE, "starting").into_response()
    }
}

#[tokio::main]
async fn main() {
    let schema_path = env("SCHEMA_PATH", "schema.sql");
    let app = Arc::new(App {
        ch_url: env("CLICKHOUSE_URL", "http://clickhouse:8123").trim_end_matches('/').to_string(),
        ch_user: env("CLICKHOUSE_USER", "otel"),
        ch_password: env("CLICKHOUSE_PASSWORD", "otel"),
        db: env("INGEST_DB", "ingest_rust"),
        schema: std::fs::read_to_string(&schema_path).expect("schema.sql readable"),
        http: reqwest::Client::builder().timeout(Duration::from_secs(30)).build().expect("http client"),
        ready: AtomicBool::new(false),
    });

    let boot = app.clone();
    tokio::spawn(async move {
        loop {
            match boot.apply_schema().await {
                Ok(()) => {
                    boot.ready.store(true, Ordering::Release);
                    eprintln!("schema applied to {}.*; ready", boot.db);
                    return;
                }
                Err(Fail::Ingest(m) | Fail::Decode(m) | Fail::MediaType(m)) => {
                    eprintln!("waiting for clickhouse: {m}");
                    tokio::time::sleep(Duration::from_secs(2)).await;
                }
            }
        }
    });

    let port = env("PORT", "4318");
    let router = Router::new()
        .route("/v1/logs", post(logs))
        .route("/v1/traces", post(traces))
        .route("/v1/metrics", post(metrics))
        .route("/healthz", get(healthz))
        .layer(DefaultBodyLimit::max(64 * 1024 * 1024))
        .with_state(app.clone());
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await.expect("bind");
    eprintln!("oteru-ingestor (rust) listening on :{port} -> {}.*", app.db);
    axum::serve(listener, router).await.expect("server");
}
