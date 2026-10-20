#!/usr/bin/env bash
# Expiry safety test for cost aggregates (#45, #47, check 4).
# Tests that after raw data expires, re-running the aggregation does NOT change
# the aggregate totals — proving incrementals pull from the existing aggregate,
# not from (now-gone) raw rows.
#
# This implementation uses the REAL dbt models from Modelo's oteru-dbt/ project.

set -euo pipefail

CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
TEST_DAY="$(date -d '1 day ago' '+%Y-%m-%d' 2>/dev/null || date -v-1d '+%Y-%m-%d' 2>/dev/null || echo '2026-10-19')"
TEST_ACTOR="sha256:v1:test-actor-expiry-$(date +%s)"
TEST_MODEL="claude-3-5-sonnet-20241022"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DBT_DIR="$ROOT/oteru-dbt"
VENV_PYTHON="$ROOT/oteru-emitter/.venv/bin/python"

q() {
  curl -sf "$CH" --data-binary "$1" 2>&1 || {
    echo "  FAIL: Query failed: $1"
    exit 1
  }
}

echo "  Running expiry safety test with real dbt models..."

# Pre-cleanup: remove any leftover test data from previous runs
q "DELETE FROM otel.otel_logs WHERE Body = 'claude_code.api_request' AND (LogAttributes['user.email'] LIKE '%test-actor-expiry%' OR LogAttributes['request_id'] LIKE 'expiry-test-req%')" >/dev/null 2>&1 || true
sleep 2

# Step 1: Insert test raw data for a specific day
echo "    1. Inserting test raw data for $TEST_DAY..."
UNIX_TS_1=$(date -d "$TEST_DAY 10:00:00" +%s 2>/dev/null || echo 1729418400)
UNIX_TS_2=$(date -d "$TEST_DAY 14:00:00" +%s 2>/dev/null || echo 1729432800)
q "INSERT INTO otel.otel_logs VALUES
  (toDateTime64('$TEST_DAY 10:00:00', 9), ${UNIX_TS_1}000000000, 'claude_code.api_request', 'claude-code', 
   {'oteru.traffic': 'test'}, 
   {'request_id': 'expiry-test-req-001', 'session.id': 'expiry-test-session', 'model': '$TEST_MODEL', 
    'cost_usd': '0.015', 'input_tokens': '1000', 'output_tokens': '500', 
    'cache_read_tokens': '0', 'cache_creation_tokens': '0', 'user.email': '$TEST_ACTOR', 
    'timeUnixNano': '${UNIX_TS_1}000000000'}),
  (toDateTime64('$TEST_DAY 14:00:00', 9), ${UNIX_TS_2}000000000, 'claude_code.api_request', 'claude-code',
   {'oteru.traffic': 'test'},
   {'request_id': 'expiry-test-req-002', 'session.id': 'expiry-test-session', 'model': '$TEST_MODEL',
    'cost_usd': '0.025', 'input_tokens': '2000', 'output_tokens': '1000',
    'cache_read_tokens': '0', 'cache_creation_tokens': '0', 'user.email': '$TEST_ACTOR',
    'timeUnixNano': '${UNIX_TS_2}000000000'})"

raw_count=$(q "SELECT count() FROM otel.otel_logs WHERE Body = 'claude_code.api_request' AND LogAttributes['user.email'] = '$TEST_ACTOR' FORMAT TSV")
echo "    Raw logs inserted: $raw_count rows"
if [ "$raw_count" != "2" ]; then
  echo "  FAIL: Expected 2 raw rows, got $raw_count"
  exit 1
fi

# Step 2: Load seeds and run dbt to build the models (stg -> int -> fct)
# Note: Final design has fct_cost_daily (permanent) and fct_cost_actor_daily (90d TTL).
# fct_cost_by_model_daily may or may not exist depending on the SHA.
echo "    2. Loading dbt seeds and running models..."
export CLICKHOUSE_HOST=localhost
export CLICKHOUSE_PORT=8123
export CLICKHOUSE_USER=otel
export CLICKHOUSE_PASSWORD=otel

cd "$DBT_DIR"
if ! "$VENV_PYTHON" -m dbt.cli.main seed --profiles-dir . >/tmp/dbt-seed-1.log 2>&1; then
  echo "  FAIL: dbt seed failed (see /tmp/dbt-seed-1.log)"
  tail -30 /tmp/dbt-seed-1.log
  exit 1
fi

# Build models - include both old and new design tables, let dbt skip what doesn't exist
if ! "$VENV_PYTHON" -m dbt.cli.main run --select int_api_request_dedup fct_cost_daily+ --profiles-dir . >/tmp/dbt-run-1.log 2>&1; then
  echo "  FAIL: dbt run failed (see /tmp/dbt-run-1.log)"
  tail -30 /tmp/dbt-run-1.log
  exit 1
fi
echo "    dbt seed + run completed (initial run)"

# Step 3: Capture the aggregate totals
# Check which tables exist (new design vs old design)
fct_daily_exists=$(curl -sf "$CH" --data-binary "SELECT count() FROM system.tables WHERE database = 'otel' AND name = 'fct_cost_daily' FORMAT TSV" 2>/dev/null || echo "0")
fct_by_model_exists=$(curl -sf "$CH" --data-binary "SELECT count() FROM system.tables WHERE database = 'otel' AND name = 'fct_cost_by_model_daily' FORMAT TSV" 2>/dev/null || echo "0")

if [ "$fct_daily_exists" = "0" ]; then
  echo "  FAIL: fct_cost_daily not created"
  exit 1
fi

initial_daily=$(q "SELECT cost_usd_micros FROM otel.fct_cost_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR' ORDER BY cost_usd_micros DESC LIMIT 1 FORMAT TSV" 2>/dev/null || echo "")
echo "    Initial fct_cost_daily total: ${initial_daily:-N/A} micros"

if [ "$fct_by_model_exists" = "1" ]; then
  initial_by_model=$(q "SELECT cost_usd_micros FROM otel.fct_cost_by_model_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR' ORDER BY cost_usd_micros DESC LIMIT 1 FORMAT TSV" 2>/dev/null || echo "")
  echo "    Initial fct_cost_by_model_daily total: ${initial_by_model:-N/A} micros"
else
  echo "    fct_cost_by_model_daily: N/A (old design, deprecated)"
  initial_by_model=""
fi

if [ -z "$initial_daily" ]; then
  echo "  FAIL: No row in fct_cost_daily for test actor/day"
  exit 1
fi

# Step 4: Delete the raw data (simulating TTL expiry)
echo "    3. Deleting raw data (simulating TTL expiry)..."
q "ALTER TABLE otel.otel_logs DELETE WHERE Body = 'claude_code.api_request' AND LogAttributes['user.email'] = '$TEST_ACTOR'"
sleep 2

raw_after=$(q "SELECT count() FROM otel.otel_logs WHERE Body = 'claude_code.api_request' AND LogAttributes['user.email'] = '$TEST_ACTOR' FORMAT TSV")
echo "    Raw logs after deletion: $raw_after rows"

# Step 5: Run dbt again (incremental should preserve existing aggregates)
echo "    4. Re-running dbt after raw data expired..."
if ! "$VENV_PYTHON" -m dbt.cli.main run --select int_api_request_dedup fct_cost_daily+ --profiles-dir . >/tmp/dbt-run-2.log 2>&1; then
  echo "  FAIL: dbt run failed on second run (see /tmp/dbt-run-2.log)"
  tail -30 /tmp/dbt-run-2.log
  exit 1
fi
echo "    dbt run completed (second run, raw data gone)"

# Step 6: Verify the aggregates are unchanged
final_daily=$(q "SELECT cost_usd_micros FROM otel.fct_cost_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR' ORDER BY cost_usd_micros DESC LIMIT 1 FORMAT TSV" 2>/dev/null || echo "")
echo "    Final fct_cost_daily total: ${final_daily:-N/A} micros"

if [ "$fct_by_model_exists" = "1" ] && [ -n "$initial_by_model" ]; then
  final_by_model=$(q "SELECT cost_usd_micros FROM otel.fct_cost_by_model_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR' ORDER BY cost_usd_micros DESC LIMIT 1 FORMAT TSV" 2>/dev/null || echo "")
  echo "    Final fct_cost_by_model_daily total: ${final_by_model:-N/A} micros"
fi

failures=0
if [ "$final_daily" = "$initial_daily" ]; then
  echo "  ok   fct_cost_daily: aggregate unchanged after raw data expired ($initial_daily micros)"
else
  echo "  FAIL: fct_cost_daily changed from $initial_daily to $final_daily"
  failures=$((failures + 1))
fi

if [ "$fct_by_model_exists" = "1" ] && [ -n "$initial_by_model" ]; then
  if [ "$final_by_model" = "$initial_by_model" ]; then
    echo "  ok   fct_cost_by_model_daily: aggregate unchanged after raw data expired ($initial_by_model micros)"
  else
    echo "  FAIL: fct_cost_by_model_daily changed from $initial_by_model to $final_by_model"
    failures=$((failures + 1))
  fi
fi

# Cleanup
q "DELETE FROM otel.fct_cost_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR'" >/dev/null 2>&1 || true
if [ "$fct_by_model_exists" = "1" ]; then
  q "DELETE FROM otel.fct_cost_by_model_daily WHERE day_utc = '$TEST_DAY' AND actor_id = '$TEST_ACTOR'" >/dev/null 2>&1 || true
fi
q "DELETE FROM otel.int_api_request_dedup WHERE actor_id = '$TEST_ACTOR'" >/dev/null 2>&1 || true

if [ "$failures" -ne 0 ]; then
  echo "  FAIL: expiry safety test failed ($failures check(s))"
  exit 1
fi

exit 0
