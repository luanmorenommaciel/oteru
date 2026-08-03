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

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMITTER="$ROOT/oteru-emitter"
TINY="$EMITTER/tests/fixtures/tiny-capture.json"

# Trace captures are built, not committed — see oteru-emitter/tests/factories.py.
TRACES="$(mktemp -t oteru-traces-XXXXXX).json"
trap 'rm -f "$TRACES"' EXIT
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

counts() {
  curl -sf "$CH" --data-binary "SELECT
      (SELECT count() FROM otel.otel_logs),
      (SELECT count() FROM otel.otel_traces),
      (SELECT count() FROM otel.otel_metrics_sum)
    FORMAT TSV"
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

# case <description> <capture> <emit> <expect-logs> <expect-traces> <expect-metrics>
case_emit() {
  local desc="$1" capture="$2" emit="$3" exp_l="$4" exp_t="$5" exp_m="$6"
  local before after
  before="$(counts)" || { fail "$desc: ClickHouse unreachable at $CH"; return; }
  IFS=$'\t' read -r l0 t0 m0 <<<"$before"

  if ! "$PY" -m oteru_emitter.cli replay "$capture" \
      --transport http --max-gap 0.2 --emit "$emit" >/dev/null 2>&1; then
    fail "$desc: emitter exited non-zero"
    return
  fi
  sleep "$SETTLE"

  after="$(counts)"
  IFS=$'\t' read -r l1 t1 m1 <<<"$after"
  local got="$((l1 - l0))/$((t1 - t0))/$((m1 - m0))"
  local want="$exp_l/$exp_t/$exp_m"
  if [ "$got" = "$want" ]; then
    echo "  ok   --emit $emit -> logs/traces/metrics +$got"
  else
    fail "--emit $emit expected +$want, got +$got"
  fi
}

echo "signal-selection e2e ($OTLP, ClickHouse)"
cd "$EMITTER" || exit 1

"$PY" - "$TRACES" <<'PY' || { echo "  FAIL: could not build the traces capture"; exit 1; }
import json, sys, pathlib
sys.path.insert(0, "tests")
from factories import traces_capture

pathlib.Path(sys.argv[1]).write_text(
    "".join(json.dumps(b) + "\n" for b in traces_capture()), encoding="utf-8"
)
PY

case_emit "logs only"    "$TINY"   "log"        3 0 0
case_emit "metrics only" "$TINY"   "metric"     0 0 1
case_emit "traces only"  "$TRACES" "trace"      0 6 0
case_emit "combined"     "$TINY"   "log,metric" 3 0 1
case_emit_rejects "partially absent" "$TINY" "log,trace"

# Replaying the same capture twice must yield two distinct traces. Trace/span
# IDs are structural OTLP fields, so nothing rotated them before and every
# replay collided with the original trace. Counted as a delta, so pre-existing
# rows in the table cannot confound it.
traces_before="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces FORMAT TSV")"
for _ in 1 2; do
  "$PY" -m oteru_emitter.cli replay "$TRACES" \
    --transport http --max-gap 0.2 --emit trace >/dev/null 2>&1 || true
done
sleep "$SETTLE"
traces_after="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces FORMAT TSV")"
new_traces=$((traces_after - traces_before))
if [ "$new_traces" -eq 4 ]; then
  echo "  ok   two replays -> 4 distinct traces (no ID collision)"
else
  fail "two replays of a 2-trace capture yielded $new_traces distinct traces (expected 4)"
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
