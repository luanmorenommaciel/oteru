#!/usr/bin/env bash
# Signal-selection e2e: proves the collector ingests any combination of signals
# without erroring, and that --emit sends exactly the selected ones.
#
# The collector needs no custom "normalization layer" for this — the OTLP
# receiver already dispatches each signal to its own pipeline, and a payload
# missing a signal simply never reaches that pipeline. This script is the
# verification artifact for that claim.
#
# Requires: `make up-clickhouse` running (collector + ClickHouse) and the venv
# from `make setup`. Run from the repo root: `make e2e-signals`.
#
# Isolation: every run tags its captures with an `oteru.e2e.run_id` resource
# attribute and filters every ClickHouse assertion by it, so live Claude Code
# telemetry or a concurrent e2e run on the same collector cannot change the
# expected row-count deltas. No environment quarantine is required.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMITTER="$ROOT/oteru-emitter"
TINY="$EMITTER/tests/fixtures/tiny-capture.json"

# Trace captures are built, not committed — see oteru-emitter/tests/factories.py.
TRACES="$(mktemp -t oteru-traces-XXXXXX).json"
SCOPED_TINY="$(mktemp -t oteru-tiny-XXXXXX).json"
SCOPED_TRACES="$(mktemp -t oteru-scoped-traces-XXXXXX).json"
MCP="$(mktemp -t oteru-mcp-XXXXXX).json"
SCOPED_MCP="$(mktemp -t oteru-scoped-mcp-XXXXXX).json"
MCP_EXPECTED="$(mktemp -t oteru-mcp-expected-XXXXXX).tsv"
trap 'rm -f "$TRACES" "$SCOPED_TINY" "$SCOPED_TRACES" "$MCP" "$SCOPED_MCP" "$MCP_EXPECTED"' EXIT
# Unique per execution; tags every replayed batch so ClickHouse assertions can
# filter to rows this run produced. date+pid keeps it portable (Git Bash/macOS/Linux).
RUN_ID="e2e-$(date +%s)-$$"
CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
OTLP="${OTLP_HTTP_ENDPOINT:-http://localhost:4318}"
SETTLE="${SETTLE_SECONDS:-6}"
if [ "${OS:-}" = "Windows_NT" ]; then
  PY="$EMITTER/.venv/Scripts/python.exe"
else
  PY="$EMITTER/.venv/bin/python"
fi

failures=0

fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }

# Row counts filtered to this run's tag — concurrent telemetry (live or
# synthetic) on the same collector contributes nothing to these numbers.
counts() {
  curl -sf "$CH" --data-binary "SELECT
      (SELECT count() FROM otel.otel_logs WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'),
      (SELECT count() FROM otel.otel_traces WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'),
      (SELECT count() FROM otel.otel_metrics_sum WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID')
    FORMAT TSV"
}

# inject_run_id <src-capture> <dst-capture>: copy a JSONL capture with the
# oteru.e2e.run_id resource attribute added to every batch.
inject_run_id() {
  "$PY" - "$1" "$2" "$RUN_ID" <<'PY'
import json, sys

src, dst, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as fh:
    batches = [json.loads(line) for line in fh if line.strip()]
for batch in batches:
    for section in ("resourceLogs", "resourceMetrics", "resourceSpans"):
        for entry in batch.get(section, []):
            attrs = entry.setdefault("resource", {}).setdefault("attributes", [])
            attrs.append({"key": "oteru.e2e.run_id", "value": {"stringValue": run_id}})
with open(dst, "w", encoding="utf-8") as fh:
    for batch in batches:
        fh.write(json.dumps(batch) + "\n")
PY
}

# The mirror of case_emit: --emit naming a signal the capture lacks has to fail
# *and* send nothing. A partially-available selection (log,trace on a capture
# with no spans) is the case that used to slip through — it left batches behind,
# so the old "nothing survived the filter" guard never fired.
# case_emit_rejects <description> <capture> <emit>
case_emit_rejects() {
  local desc="$1" capture="$2" emit="$3"
  local before after
  before="$(counts)" || { fail "$desc: ClickHouse unreachable at $CH"; return; }
  IFS=$'\t' read -r l0 t0 m0 <<<"$before"

  if "$PY" -m oteru_emitter.cli replay "$capture" \
      --transport http --max-gap 0.2 --emit "$emit" >/dev/null 2>&1; then
    fail "$desc: --emit $emit should have exited non-zero"
    return
  fi
  sleep "$SETTLE"

  after="$(counts)"
  IFS=$'\t' read -r l1 t1 m1 <<<"$after"
  local got="$((l1 - l0))/$((t1 - t0))/$((m1 - m0))"
  if [ "$got" = "0/0/0" ]; then
    echo "  ok   --emit $emit -> rejected, nothing sent"
  else
    fail "--emit $emit was rejected but still sent +$got"
  fi
}

# case <description> <capture> <emit> <expect-logs> <expect-traces> <expect-metrics> [transport]
# transport defaults to http; pass grpc to exercise the :4317 receiver instead.
case_emit() {
  local desc="$1" capture="$2" emit="$3" exp_l="$4" exp_t="$5" exp_m="$6"
  local transport="${7:-http}"
  local before after
  before="$(counts)" || { fail "$desc: ClickHouse unreachable at $CH"; return; }
  IFS=$'\t' read -r l0 t0 m0 <<<"$before"

  if ! "$PY" -m oteru_emitter.cli replay "$capture" \
      --transport "$transport" --max-gap 0.2 --emit "$emit" >/dev/null 2>&1; then
    fail "$desc: emitter exited non-zero"
    return
  fi
  sleep "$SETTLE"

  after="$(counts)"
  IFS=$'\t' read -r l1 t1 m1 <<<"$after"
  local got="$((l1 - l0))/$((t1 - t0))/$((m1 - m0))"
  local want="$exp_l/$exp_t/$exp_m"
  if [ "$got" = "$want" ]; then
    echo "  ok   [$transport] --emit $emit -> logs/traces/metrics +$got"
  else
    fail "[$transport] --emit $emit expected +$want, got +$got"
  fi
}

echo "signal-selection e2e ($OTLP + gRPC :4317, ClickHouse)"
cd "$EMITTER" || exit 1

"$PY" - "$TRACES" <<'PY' || { echo "  FAIL: could not build the traces capture"; exit 1; }
import json, sys, pathlib
sys.path.insert(0, "tests")
from factories import traces_capture

pathlib.Path(sys.argv[1]).write_text(
    "".join(json.dumps(b) + "\n" for b in traces_capture()), encoding="utf-8"
)
PY

inject_run_id "$TINY" "$SCOPED_TINY" \
  || { echo "  FAIL: could not tag the tiny capture"; exit 1; }
inject_run_id "$TRACES" "$SCOPED_TRACES" \
  || { echo "  FAIL: could not tag the traces capture"; exit 1; }

case_emit "logs only"    "$SCOPED_TINY"   "log"        3 0 0
case_emit "metrics only" "$SCOPED_TINY"   "metric"     0 0 1
case_emit "traces only"  "$SCOPED_TRACES" "trace"      0 6 0
case_emit "combined"     "$SCOPED_TINY"   "log,metric" 3 0 1
case_emit_rejects "partially absent" "$SCOPED_TINY" "log,trace"

# Same signals over gRPC (:4317). Both receivers feed the same per-signal
# pipelines, so the deltas must match the HTTP cases exactly (#40).
case_emit "logs only (grpc)"    "$SCOPED_TINY"   "log"        3 0 0 grpc
case_emit "metrics only (grpc)" "$SCOPED_TINY"   "metric"     0 0 1 grpc
case_emit "traces only (grpc)"  "$SCOPED_TRACES" "trace"      0 6 0 grpc
case_emit "combined (grpc)"     "$SCOPED_TINY"   "log,metric" 3 0 1 grpc

# Replaying the same capture twice must yield two distinct traces. Trace/span
# IDs are structural OTLP fields, so nothing rotated them before and every
# replay collided with the original trace. Run-scoped query, so neither
# pre-existing rows nor concurrent telemetry can confound it.
traces_before="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces \
   WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID' FORMAT TSV")"
for _ in 1 2; do
  if ! "$PY" -m oteru_emitter.cli replay "$SCOPED_TRACES" \
    --transport http --max-gap 0.2 --emit trace >/dev/null 2>&1; then
    fail "trace replay exited non-zero"
  fi
done
sleep "$SETTLE"
traces_after="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces \
   WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID' FORMAT TSV")"
new_traces=$((traces_after - traces_before))
if [ "$new_traces" -eq 4 ]; then
  echo "  ok   two replays -> 4 distinct traces (no ID collision)"
else
  fail "two replays of a 2-trace capture yielded $new_traces distinct traces (expected 4)"
fi

# MCP calls as first-class rows (#42): replay the MCP fixture and read it back
# through the otel.mcp_calls view — per-method counts must equal what the
# fixture declares, the failed tool call must surface as failed, and the
# client/server sides of one call must join on (trace_id, request_id).
"$PY" - "$MCP" "$MCP_EXPECTED" <<'PY' || fail "could not build the MCP capture"
import json, sys, pathlib
sys.path.insert(0, "tests")
from factories import MCP_EXPECTED_METHODS, mcp_capture

pathlib.Path(sys.argv[1]).write_text(
    "".join(json.dumps(b) + "\n" for b in mcp_capture()), encoding="utf-8"
)
pathlib.Path(sys.argv[2]).write_text(
    "".join(f"{m}\t{n}\n" for m, n in sorted(MCP_EXPECTED_METHODS.items())), encoding="utf-8"
)
PY
inject_run_id "$MCP" "$SCOPED_MCP" || fail "could not tag the MCP capture"
if ! curl -sf "$CH" --data-binary @"$ROOT/oteru-collector/clickhouse/views/mcp_calls.sql" >/dev/null; then
  fail "could not apply the otel.mcp_calls view"
elif ! "$PY" -m oteru_emitter.cli replay "$SCOPED_MCP" --profile generic \
    --transport http --max-gap 0.2 >/dev/null 2>&1; then
  fail "MCP replay exited non-zero"
else
  sleep "$SETTLE"
  scope="resource_attributes['oteru.e2e.run_id'] = '$RUN_ID'"
  got="$(curl -sf "$CH" --data-binary \
    "SELECT method, count() FROM otel.mcp_calls WHERE $scope GROUP BY method ORDER BY method FORMAT TSV")"
  if [ "$got" = "$(cat "$MCP_EXPECTED")" ]; then
    echo "  ok   otel.mcp_calls -> per-method counts match the fixture"
  else
    fail "otel.mcp_calls per-method counts: got [$got], expected [$(cat "$MCP_EXPECTED")]"
  fi
  failed="$(curl -sf "$CH" --data-binary \
    "SELECT tool_name, error_type FROM otel.mcp_calls WHERE $scope AND failed FORMAT TSV")"
  if [ "$failed" = $'refund_order\ttool_error' ]; then
    echo "  ok   otel.mcp_calls -> failed tool call surfaces with its error.type"
  else
    fail "otel.mcp_calls failed rows: got [$failed]"
  fi
  joined="$(curl -sf "$CH" --data-binary \
    "SELECT count() FROM otel.mcp_calls c JOIN otel.mcp_calls s
       ON c.trace_id = s.trace_id AND c.request_id = s.request_id AND s.parent_span_id = c.span_id
     WHERE c.side = 'client' AND s.side = 'server'
       AND c.$scope AND s.$scope FORMAT TSV")"
  if [ "$joined" = "1" ]; then
    echo "  ok   otel.mcp_calls -> client and server sides of a call join"
  else
    fail "otel.mcp_calls client/server join: got $joined row(s), expected 1"
  fi
fi

# A payload with no records at all must be accepted, not rejected.
for path in v1/logs v1/metrics v1/traces; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$OTLP/$path" \
    -H 'content-type: application/json' --data '{}')"
  if [ "$code" = "200" ]; then
    echo "  ok   empty payload POST /$path -> 200"
  else
    fail "empty payload POST /$path -> $code (expected 200)"
  fi
done

if [ "$failures" -ne 0 ]; then
  echo
  echo "e2e-signals: $failures check(s) failed."
  exit 1
fi
echo
echo "e2e-signals: OK — every signal combination ingested, no collector error."
