#!/usr/bin/env bash
# Check 6: expiring fct_cost_actor_daily rows must not change fct_cost_daily.
# Insert raw → dbt --full-refresh → snapshot permanent total → delete actor
# rows (local TTL stand-in) → dbt incremental → permanent total identical.
# Never SKIP when fct_cost_actor_daily exists. A zero initial total or an
# empty actor table is FAIL — never PASS comparing empty with empty.

set -euo pipefail

# shellcheck source=check_cost_retention_lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_cost_retention_lib.sh"

TEST_DAY="$(test_day)"
TEST_ACTOR="sha256:v1:test-actor-ttl-$(date +%s)"
TEST_PREFIX="actor-ttl-req-$(date +%s)"

echo "  Running actor TTL expiry test (permanent totals must survive)..."
echo "    test day (today() - 1): $TEST_DAY"

echo "    1. Inserting test raw data for $TEST_DAY (INSERT … SELECT, columns from DESCRIBE)..."
if ! insert_test_raw "$TEST_ACTOR" "$TEST_PREFIX"; then
  echo "  FAIL: could not insert test raw rows into otel.otel_logs"
  exit 1
fi
raw_count="$(ch_q "SELECT count() FROM otel.otel_logs WHERE LogAttributes['request_id'] LIKE '${TEST_PREFIX}%' FORMAT TSV")"
echo "    Raw logs inserted: $raw_count rows"
if [ "$raw_count" != "2" ]; then
  echo "  FAIL: Expected 2 raw rows, got $raw_count"
  exit 1
fi

echo "    2. dbt seed + run --full-refresh (int_api_request_dedup+)..."
if ! dbt_seed >/tmp/dbt-seed-actor-1.log 2>&1; then
  echo "  FAIL: dbt seed failed (see /tmp/dbt-seed-actor-1.log)"
  tail -30 /tmp/dbt-seed-actor-1.log
  exit 1
fi
if ! dbt_run_models --full-refresh >/tmp/dbt-run-actor-1.log 2>&1; then
  echo "  FAIL: dbt run --full-refresh failed (see /tmp/dbt-run-actor-1.log)"
  tail -30 /tmp/dbt-run-actor-1.log
  exit 1
fi

if ! table_exists fct_cost_daily || ! table_exists fct_cost_actor_daily; then
  echo "  FAIL: fct_cost_daily or fct_cost_actor_daily missing after dbt run"
  exit 1
fi

initial_daily="$(sum_cost fct_cost_daily)"
initial_actor="$(sum_cost fct_cost_actor_daily)"
actor_rows="$(actor_row_count)"
if ! require_nonzero_reference "$initial_daily" "$initial_actor" "$actor_rows" "check 6 initial"; then
  exit 1
fi

echo "    3. Deleting fct_cost_actor_daily rows (simulating 90-day TTL expiry)..."
ch_q "TRUNCATE TABLE otel.fct_cost_actor_daily" >/dev/null
actor_after="$(sum_cost fct_cost_actor_daily)"
actor_rows_after="$(actor_row_count)"
echo "    totals (check 6 after actor truncate): fct_cost_daily=$(sum_cost fct_cost_daily) micros  fct_cost_actor_daily=$actor_after micros  actor_rows=$actor_rows_after"

echo "    4. Re-running dbt incrementally..."
if ! dbt_run_models >/tmp/dbt-run-actor-2.log 2>&1; then
  echo "  FAIL: dbt run failed on second run (see /tmp/dbt-run-actor-2.log)"
  tail -30 /tmp/dbt-run-actor-2.log
  exit 1
fi

final_daily="$(sum_cost fct_cost_daily)"
final_actor="$(sum_cost fct_cost_actor_daily)"
final_actor_rows="$(actor_row_count)"
echo "    totals (check 6 final): fct_cost_daily=${final_daily:-empty} micros  fct_cost_actor_daily=${final_actor:-empty} micros  actor_rows=${final_actor_rows:-empty}"

if [ "$final_daily" = "$initial_daily" ]; then
  echo "  ok   fct_cost_daily: permanent total unchanged after actor rows expired ($initial_daily micros)"
  exit 0
fi

echo "  FAIL: fct_cost_daily permanent total changed from $initial_daily to $final_daily after actor expiry"
exit 1
