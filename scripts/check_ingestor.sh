#!/usr/bin/env bash
# Ingestor conformance (oteru-ingestor/SPEC.md): the single black-box suite every
# implementation — python (#24), go (#25), rust (#26) — must pass. It only
# speaks OTLP/HTTP to the ingestor and SQL to ClickHouse, so it cannot tell the
# languages apart: that is the point.
#
# Usage: bash scripts/check_ingestor.sh python|go|rust   (or make ingestor-check IMPL=…)
# Requires: the ingestor + ClickHouse up (`make up-ingestor IMPL=…`) and the
# emitter venv from `make setup`. Isolation as in check_signals_e2e.sh: every
# payload carries a per-run `oteru.e2e.run_id` resource attribute.

set -uo pipefail

IMPL="${1:-${IMPL:-}}"
case "$IMPL" in
  python) PORT=14318 ;;
  go) PORT=24318 ;;
  rust) PORT=34318 ;;
  *) echo "usage: $0 python|go|rust" >&2; exit 2 ;;
esac
DB="ingest_$IMPL"
URL="${INGESTOR_URL:-http://localhost:$PORT}"
CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
SETTLE="${SETTLE_SECONDS:-2}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMITTER="$ROOT/oteru-emitter"
if [ "${OS:-}" = "Windows_NT" ]; then PY="$EMITTER/.venv/Scripts/python.exe"; else PY="$EMITTER/.venv/bin/python"; fi
WORK="$(mktemp -d -t oteru-ingestor-XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
RUN_ID="ingest-$IMPL-$(date +%s)-$$"

failures=0
fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }
ok() { echo "  ok   $*"; }
q() { curl -sf "$CH" --data-binary "$1"; }
scoped="ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'"
count() { q "SELECT count() FROM $DB.$1 WHERE $scoped ${2:+AND $2} FORMAT TSV"; }

echo "ingestor conformance — $IMPL ($URL → ClickHouse $DB)"

# 1. health ------------------------------------------------------------------------
health=""
for _ in $(seq 1 30); do
  health="$(curl -s -o /dev/null -w '%{http_code}' "$URL/healthz")"
  [ "$health" = "200" ] && break
  sleep 1
done
if [ "$health" = "200" ]; then ok "GET /healthz -> 200"; else
  fail "GET /healthz -> $health (is \`make up-ingestor IMPL=$IMPL\` running?)"
  echo; echo "ingestor-check: $IMPL not reachable."; exit 1
fi

# Fixtures: built from the emitter's factories, tagged with the run id, written as
# OTLP/JSON captures (for replay) and as raw request bodies (for curl).
cd "$EMITTER" || exit 1
"$PY" - "$WORK" "$RUN_ID" <<'PY' || { echo "  FAIL: could not build fixtures"; exit 1; }
import gzip, json, pathlib, sys
sys.path.insert(0, "tests")
from factories import traces_capture
from oteru_emitter.model.otlp import to_request

work, run_id = pathlib.Path(sys.argv[1]), sys.argv[2]
tag = {"key": "oteru.e2e.run_id", "value": {"stringValue": run_id}}

def tagged(batches):
    for b in batches:
        for section in ("resourceLogs", "resourceMetrics", "resourceSpans"):
            for entry in b.get(section, []):
                entry.setdefault("resource", {}).setdefault("attributes", []).append(dict(tag))
    return batches

def capture(name, batches):
    (work / name).write_text("".join(json.dumps(b) + "\n" for b in tagged(batches)), encoding="utf-8")

tiny = [json.loads(l) for l in open("tests/fixtures/tiny-capture.json", encoding="utf-8") if l.strip()]
capture("tiny.json", tiny)                                # 3 log records + 1 sum point
capture("traces.json", traces_capture())                  # 6 spans, typed attrs, parent links
typed = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "typed-client"}}]},
          "scopeSpans": [{"scope": {"name": "conf"}, "spans": [{
              "traceId": "3d5f7a9c1e3b5d7f9a1c3e5b7d9f1a3c", "spanId": "c1d2e3f4a5b6c7d9", "name": "typed", "kind": 2,
              "startTimeUnixNano": "1752620000000000000", "endTimeUnixNano": "1752620001000000000",
              "attributes": [{"key": "gen_ai.usage.input_tokens", "value": {"intValue": "820"}},
                             {"key": "gen_ai.cost.total_cost", "value": {"doubleValue": 0.00214}},
                             {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4.1"}}],
              "status": {"code": 2, "message": "boom"}}]}]}]}
capture("typed.json", [typed])                            # int / double / string attrs, SERVER, ERROR

res = lambda svc: {"attributes": [{"key": "service.name", "value": {"stringValue": svc}}, dict(tag)]}
log = {"resourceLogs": [{"resource": res("json-client"), "scopeLogs": [{"scope": {"name": "conf"},
       "logRecords": [{"timeUnixNano": "1752620000000000000", "body": {"stringValue": "via-json"},
                       "attributes": [{"key": "k", "value": {"intValue": "7"}}]}]}]}]}
(work / "logs.json").write_text(json.dumps(log), encoding="utf-8")

gz = {"resourceLogs": [{"resource": res("gzip-client"), "scopeLogs": [{"scope": {"name": "conf"},
      "logRecords": [{"timeUnixNano": "1752620000000000000", "body": {"stringValue": "via-gzip"}}]}]}]}
(work / "logs.pb.gz").write_bytes(gzip.compress(to_request("logs", gz).SerializeToString()))

point = {"timeUnixNano": "1752620000000000000", "startTimeUnixNano": "1752619990000000000"}
metrics = {"resourceMetrics": [{"resource": res("hist-client"), "scopeMetrics": [{"scope": {"name": "conf"}, "metrics": [
    {"name": "conf.sum", "sum": {"aggregationTemporality": 1, "isMonotonic": True,
                                  "dataPoints": [dict(point, asInt="5")]}},
    {"name": "conf.hist", "histogram": {"aggregationTemporality": 1,
                                        "dataPoints": [dict(point, count="2", sum=3.0,
                                                            bucketCounts=["1", "1"], explicitBounds=[1.0])]}},
]}]}]}
(work / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
PY

replay() {
  "$PY" -m oteru_emitter.cli replay "$WORK/$1" --profile generic --transport http \
    --endpoint "$URL" --max-gap 0.1 ${2:+--emit "$2"} >/dev/null 2>&1
}

# 2. signals over protobuf, replayed by the emitter ----------------------------------
replay tiny.json || fail "replay of the logs+metrics capture exited non-zero"
replay traces.json || fail "replay of the traces capture exited non-zero"
replay typed.json || fail "replay of the typed-attributes capture exited non-zero"
sleep "$SETTLE"
got="$(count otel_logs "ServiceName != 'json-client' AND ServiceName != 'gzip-client'")/$(count otel_metrics_sum "MetricName != 'conf.sum'")/$(count otel_traces)"
if [ "$got" = "3/1/7" ]; then ok "protobuf: logs/metrics/spans landed 3/1/7"; else fail "protobuf: logs/metrics/spans = $got, expected 3/1/7"; fi

# 3. trace structure -----------------------------------------------------------------
orphans="$(q "SELECT count() FROM $DB.otel_traces c WHERE $scoped AND ParentSpanId != ''
              AND (TraceId, ParentSpanId) NOT IN (SELECT TraceId, SpanId FROM $DB.otel_traces WHERE $scoped) FORMAT TSV")"
hexids="$(q "SELECT countIf(match(TraceId, '^[0-9a-f]{32}\$') AND match(SpanId, '^[0-9a-f]{16}\$')) = count()
             FROM $DB.otel_traces WHERE $scoped FORMAT TSV")"
if [ "$orphans" = "0" ] && [ "$hexids" = "1" ]; then ok "spans: lowercase-hex IDs, every parent resolvable"; else
  fail "spans: orphans=$orphans, all-hex=$hexids"; fi
kinds="$(q "SELECT arrayStringConcat(arraySort(groupUniqArray(SpanKind)), ',') FROM $DB.otel_traces WHERE $scoped FORMAT TSV")"
if [ "$kinds" = "Internal,Server" ]; then ok "spans: SpanKind rendered as Internal/Server"; else fail "spans: SpanKind set = [$kinds]"; fi
dur="$(q "SELECT Duration FROM $DB.otel_traces WHERE $scoped AND SpanName = 'claude_code.interaction' AND SpanAttributes['interaction.sequence'] = '1' FORMAT TSV")"
if [ "$dur" = "8400000000" ]; then ok "spans: Duration = end - start in ns"; else fail "spans: Duration = [$dur], expected 8400000000"; fi

# 4. attribute rendering -------------------------------------------------------------
attrs="$(q "SELECT SpanAttributes['gen_ai.usage.input_tokens'], SpanAttributes['gen_ai.cost.total_cost'], SpanAttributes['gen_ai.request.model']
            FROM $DB.otel_traces WHERE $scoped AND SpanName = 'typed' FORMAT TSV")"
bool="$(q "SELECT DISTINCT SpanAttributes['success'] FROM $DB.otel_traces WHERE $scoped AND SpanName = 'claude_code.llm_request' FORMAT TSV")"
if [ "$attrs" = $'820\t0.00214\tgpt-4.1' ] && [ "$bool" = "true" ]; then ok "attributes: int 820, double 0.00214, string, bool true"; else
  fail "attributes: got [$attrs] bool=[$bool]"; fi
row="$(q "SELECT ServiceName, StatusCode, StatusMessage FROM $DB.otel_traces WHERE $scoped AND SpanName = 'typed' FORMAT TSV")"
if [ "$row" = $'typed-client\tError\tboom' ]; then ok "ServiceName from the resource; status Error + message"; else fail "typed span row = [$row]"; fi

# 5. OTLP/JSON -----------------------------------------------------------------------
code="$(curl -s -o "$WORK/r" -w '%{http_code}' -X POST "$URL/v1/logs" -H 'Content-Type: application/json' --data-binary @"$WORK/logs.json")"
sleep "$SETTLE"
row="$(q "SELECT Body, LogAttributes['k'] FROM $DB.otel_logs WHERE $scoped AND ServiceName = 'json-client' FORMAT TSV")"
if [ "$code" = "200" ] && [ "$row" = $'via-json\t7' ]; then ok "OTLP/JSON accepted and stored"; else fail "OTLP/JSON: http $code, row [$row]"; fi

# 6. gzip ----------------------------------------------------------------------------
code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/v1/logs" -H 'Content-Type: application/x-protobuf' \
  -H 'Content-Encoding: gzip' --data-binary @"$WORK/logs.pb.gz")"
sleep "$SETTLE"
if [ "$code" = "200" ] && [ "$(count otel_logs "ServiceName = 'gzip-client'")" = "1" ]; then ok "gzip protobuf accepted and stored"; else
  fail "gzip: http $code, rows $(count otel_logs "ServiceName = 'gzip-client'")"; fi

# 7. edge statuses -------------------------------------------------------------------
status() { curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/v1/$1" -H "Content-Type: $2" --data-binary "$3"; }
got="$(status logs application/x-protobuf '')/$(status traces application/json '{}')/$(status metrics application/x-protobuf 'not-a-protobuf')/$(status logs text/plain 'hello')"
if [ "$got" = "200/200/400/415" ]; then ok "empty->200, {}->200, garbage->400, text/plain->415"; else fail "edge statuses = $got, expected 200/200/400/415"; fi

# 8. unsupported metric type -> partial success ---------------------------------------
code="$(curl -s -o "$WORK/hist" -w '%{http_code}' -X POST "$URL/v1/metrics" -H 'Content-Type: application/json' --data-binary @"$WORK/metrics.json")"
sleep "$SETTLE"
rejected="$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(int(d.get('partialSuccess',{}).get('rejectedDataPoints',0)))" "$WORK/hist" 2>/dev/null)"
stored="$(count otel_metrics_sum "MetricName = 'conf.sum'")"
if [ "$code" = "200" ] && [ "$rejected" = "1" ] && [ "$stored" = "1" ]; then ok "histogram rejected via partialSuccess (1), sum point stored"; else
  fail "partial success: http $code, rejectedDataPoints=[$rejected], sum rows=$stored"; fi

if [ "$failures" -ne 0 ]; then
  echo; echo "ingestor-check ($IMPL): $failures check(s) failed."; exit 1
fi
echo; echo "ingestor-check ($IMPL): OK — conforms to oteru-ingestor/SPEC.md."
