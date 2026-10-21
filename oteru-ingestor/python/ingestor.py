"""oteru-ingestor, Python implementation (#24) — the reference for SPEC.md.

OTLP/HTTP in (protobuf or JSON, optionally gzip), ClickHouse out, through the
HTTP interface with `INSERT … FORMAT JSONEachRow` — one INSERT per table per
request. Deliberately plain: stdlib HTTP server, `opentelemetry-proto` to
decode, `requests` to write. The Go (#25) and Rust (#26) implementations are
held to the same black-box suite (scripts/check_ingestor.sh); this one is the
readable baseline they are benchmarked against (#3).
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from google.protobuf import json_format
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

log = logging.getLogger("oteru-ingestor")

CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123").rstrip("/")
CLICKHOUSE_AUTH = (
    os.environ.get("CLICKHOUSE_USER", "otel"),
    os.environ.get("CLICKHOUSE_PASSWORD", "otel"),
)
DB = os.environ.get("INGEST_DB", "ingest_python")
PORT = int(os.environ.get("PORT", "4318"))
SCHEMA = Path(os.environ.get("SCHEMA_PATH", Path(__file__).with_name("schema.sql")))

SIGNALS = {
    "/v1/logs": (logs_service_pb2.ExportLogsServiceRequest, logs_service_pb2.ExportLogsServiceResponse),
    "/v1/traces": (trace_service_pb2.ExportTraceServiceRequest, trace_service_pb2.ExportTraceServiceResponse),
    "/v1/metrics": (
        metrics_service_pb2.ExportMetricsServiceRequest,
        metrics_service_pb2.ExportMetricsServiceResponse,
    ),
}
SPAN_KINDS = ("Unspecified", "Internal", "Server", "Client", "Producer", "Consumer")
STATUS_CODES = ("Unset", "Ok", "Error")
# OTLP/JSON carries trace/span IDs as hex; protobuf JSON mapping expects base64.
ID_FIELDS = {"traceId": 16, "spanId": 8, "parentSpanId": 8}

ready = threading.Event()


class IngestError(Exception):
    """ClickHouse refused or could not be reached — answered as 503."""


# --- ClickHouse -------------------------------------------------------------------


def clickhouse(sql: str, body: bytes | None = None) -> None:
    try:
        resp = requests.post(
            CLICKHOUSE_URL,
            params={"query": sql} if body is not None else None,
            data=body if body is not None else sql.encode(),
            auth=CLICKHOUSE_AUTH,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise IngestError(f"clickhouse unreachable: {exc}") from exc
    if resp.status_code != 200:
        raise IngestError(f"clickhouse {resp.status_code}: {resp.text.strip()[:300]}")


def apply_schema() -> None:
    statements = SCHEMA.read_text(encoding="utf-8").replace("{db}", DB).split("\n;\n")
    for statement in statements:
        sql = "\n".join(ln for ln in statement.splitlines() if not ln.strip().startswith("--")).strip()
        if sql:
            clickhouse(sql)


def insert(table: str, rows: list[dict]) -> None:
    if rows:
        body = "\n".join(json.dumps(r, separators=(",", ":")) for r in rows).encode()
        clickhouse(f"INSERT INTO {DB}.{table} FORMAT JSONEachRow", body)


# --- value rendering (SPEC.md, "Mapping") -----------------------------------------


def fmt_double(x: float) -> str:
    if x != x or x in (float("inf"), float("-inf")):
        return str(x)
    s = format(Decimal(repr(x)), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def json_value(v) -> object:
    kind = v.WhichOneof("value")
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return v.bool_value
    if kind == "int_value":
        return v.int_value
    if kind == "double_value":
        return v.double_value
    if kind == "bytes_value":
        return base64.b64encode(v.bytes_value).decode()
    if kind == "array_value":
        return [json_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: json_value(kv.value) for kv in v.kvlist_value.values}
    return None


def render(v) -> str:
    kind = v.WhichOneof("value")
    if kind == "string_value":
        return v.string_value
    if kind == "bool_value":
        return "true" if v.bool_value else "false"
    if kind == "int_value":
        return str(v.int_value)
    if kind == "double_value":
        return fmt_double(v.double_value)
    if kind is None:
        return ""
    return json.dumps(json_value(v), separators=(",", ":"))


def attrs(kvs) -> dict[str, str]:
    return {kv.key: render(kv.value) for kv in kvs}


def hexid(raw: bytes) -> str:
    return raw.hex() if raw and any(raw) else ""


def ts(ns: int) -> str:
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


# --- signal -> rows ---------------------------------------------------------------


def log_rows(req) -> list[dict]:
    rows = []
    for rl in req.resource_logs:
        res = attrs(rl.resource.attributes)
        for sl in rl.scope_logs:
            for r in sl.log_records:
                rows.append(
                    {
                        "Timestamp": ts(r.time_unix_nano or r.observed_time_unix_nano),
                        "TraceId": hexid(r.trace_id),
                        "SpanId": hexid(r.span_id),
                        "TraceFlags": r.flags & 0xFF,
                        "SeverityText": r.severity_text,
                        "SeverityNumber": int(r.severity_number),
                        "ServiceName": res.get("service.name", ""),
                        "Body": render(r.body),
                        "ResourceAttributes": res,
                        "ScopeName": sl.scope.name,
                        "ScopeVersion": sl.scope.version,
                        "LogAttributes": attrs(r.attributes),
                        "EventName": getattr(r, "event_name", ""),
                    }
                )
    return rows


def span_rows(req) -> list[dict]:
    rows = []
    for rs in req.resource_spans:
        res = attrs(rs.resource.attributes)
        for ss in rs.scope_spans:
            for sp in ss.spans:
                kind = SPAN_KINDS[sp.kind] if 0 <= sp.kind < len(SPAN_KINDS) else "Unspecified"
                code = STATUS_CODES[sp.status.code] if 0 <= sp.status.code < 3 else "Unset"
                rows.append(
                    {
                        "Timestamp": ts(sp.start_time_unix_nano),
                        "TraceId": hexid(sp.trace_id),
                        "SpanId": hexid(sp.span_id),
                        "ParentSpanId": hexid(sp.parent_span_id),
                        "TraceState": sp.trace_state,
                        "SpanName": sp.name,
                        "SpanKind": kind,
                        "ServiceName": res.get("service.name", ""),
                        "ResourceAttributes": res,
                        "ScopeName": ss.scope.name,
                        "ScopeVersion": ss.scope.version,
                        "SpanAttributes": attrs(sp.attributes),
                        "Duration": max(sp.end_time_unix_nano - sp.start_time_unix_nano, 0),
                        "StatusCode": code,
                        "StatusMessage": sp.status.message,
                    }
                )
    return rows


def metric_rows(req) -> tuple[list[dict], list[dict], int, set[str]]:
    sums, gauges, rejected, rejected_types = [], [], 0, set()
    for rm in req.resource_metrics:
        res = attrs(rm.resource.attributes)
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                kind = m.WhichOneof("data")
                base = {
                    "ResourceAttributes": res,
                    "ServiceName": res.get("service.name", ""),
                    "ScopeName": sm.scope.name,
                    "ScopeVersion": sm.scope.version,
                    "MetricName": m.name,
                    "MetricDescription": m.description,
                    "MetricUnit": m.unit,
                }
                if kind in ("sum", "gauge"):
                    data = getattr(m, kind)
                    for p in data.data_points:
                        value = p.as_int if p.WhichOneof("value") == "as_int" else p.as_double
                        row = dict(
                            base,
                            Attributes=attrs(p.attributes),
                            StartTimeUnix=ts(p.start_time_unix_nano),
                            TimeUnix=ts(p.time_unix_nano),
                            Value=float(value),
                        )
                        if kind == "sum":
                            row["AggregationTemporality"] = int(data.aggregation_temporality)
                            row["IsMonotonic"] = data.is_monotonic
                            sums.append(row)
                        else:
                            gauges.append(row)
                elif kind is not None:
                    rejected += len(getattr(m, kind).data_points)
                    rejected_types.add(kind)
    return sums, gauges, rejected, rejected_types


# --- HTTP ---------------------------------------------------------------------------


def hex_to_b64(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            n = ID_FIELDS.get(k)
            if n and isinstance(v, str) and len(v) == 2 * n:
                try:
                    out[k] = base64.b64encode(bytes.fromhex(v)).decode()
                    continue
                except ValueError:
                    pass
            out[k] = hex_to_b64(v)
        return out
    if isinstance(node, list):
        return [hex_to_b64(x) for x in node]
    return node


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # route access logs through logging
        log.debug(fmt, *args)

    def reply(self, code: int, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, b"ok") if ready.is_set() else self.reply(503, b"starting")
        else:
            self.reply(404, b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        signal = SIGNALS.get(self.path)
        if signal is None:
            return self.reply(404, b"unknown path")
        if not ready.is_set():
            return self.reply(503, b"starting")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype not in ("application/x-protobuf", "application/json"):
            return self.reply(415, f"unsupported content type {ctype!r}".encode())
        request_cls, response_cls = signal
        try:
            if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            req = request_cls()
            if ctype == "application/json":
                if raw.strip():
                    json_format.ParseDict(hex_to_b64(json.loads(raw)), req, ignore_unknown_fields=True)
            else:
                req.ParseFromString(raw)
        except (DecodeError, json_format.ParseError, ValueError, OSError) as exc:
            return self.reply(400, f"cannot decode body: {exc}".encode())

        resp = response_cls()
        try:
            if self.path == "/v1/logs":
                insert("otel_logs", log_rows(req))
            elif self.path == "/v1/traces":
                insert("otel_traces", span_rows(req))
            else:
                sums, gauges, rejected, types = metric_rows(req)
                insert("otel_metrics_sum", sums)
                insert("otel_metrics_gauge", gauges)
                if rejected:
                    resp.partial_success.rejected_data_points = rejected
                    resp.partial_success.error_message = (
                        f"unsupported metric type(s): {', '.join(sorted(types))}"
                    )
        except IngestError as exc:
            log.warning("%s", exc)
            return self.reply(503, str(exc).encode())

        if ctype == "application/json":
            body = json_format.MessageToJson(resp, indent=None).encode()
        else:
            body = resp.SerializeToString()
        self.reply(200, body, ctype)


def bootstrap() -> None:
    """Applies the schema, retrying until ClickHouse answers; then /healthz is 200."""
    while True:
        try:
            apply_schema()
            ready.set()
            log.info("schema applied to %s.*; ready", DB)
            return
        except IngestError as exc:
            log.info("waiting for clickhouse: %s", exc)
            time.sleep(2)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(message)s")
    threading.Thread(target=bootstrap, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("oteru-ingestor (python) listening on :%d -> %s.*", PORT, DB)
    server.serve_forever()


if __name__ == "__main__":
    main()
