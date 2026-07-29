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
TRACES="$EMITTER/tests/fixtures/traces-capture.json"
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

case_emit "logs only"    "$TINY"   "log"        3 0 0
case_emit "metrics only" "$TINY"   "metric"     0 0 1
case_emit "traces only"  "$TRACES" "trace"      0 5 0
case_emit "combined"     "$TINY"   "log,metric" 3 0 1

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
