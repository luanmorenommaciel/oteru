#!/usr/bin/env bash
# Check 6: expiring fct_cost_actor_daily rows must not change fct_cost_daily.
# Insert raw → dbt --full-refresh → snapshot permanent total → delete actor
# rows (local TTL stand-in) → dbt incremental → permanent total identical.
# Never SKIP when fct_cost_actor_daily exists. A zero initial total is FAIL.

set -euo pipefail

# shellcheck source=check_cost_retention_lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_cost_retention_lib.sh"

TEST_DAY="$(yesterday_utc)"
TEST_ACTOR="sha256:v1:test-actor-ttl-$(date +%s)"
TEST_PREFIX="actor-ttl-req-$(date +%s)"

echo "  Running actor TTL expiry test (permanent totals must survive)..."

echo "    1. Inserting test raw data for $TEST_DAY..."
if ! insert_test_raw "$TEST_DAY" "$TEST_ACTOR" "$TEST_PREFIX"; then
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
echo "    Initial fct_cost_daily total: ${initial_daily:-N/A} micros"
echo "    Initial fct_cost_actor_daily total: ${initial_actor:-N/A} micros"

if [ -z "${initial_daily:-}" ] || [ "$initial_daily" = "0" ]; then
  echo "  FAIL: fct_cost_daily sum(cost_usd_micros) is ${initial_daily:-empty} after --full-refresh (must be > 0; a zero total is never a pass)"
  exit 1
fi
if [ -z "${initial_actor:-}" ] || [ "$initial_actor" = "0" ]; then
  echo "  FAIL: fct_cost_actor_daily sum(cost_usd_micros) is ${initial_actor:-empty} after --full-refresh (must be > 0 before expiry)"
  exit 1
fi

echo "    3. Deleting fct_cost_actor_daily rows (simulating 90-day TTL expiry)..."
ch_q "TRUNCATE TABLE otel.fct_cost_actor_daily" >/dev/null
actor_after="$(sum_cost fct_cost_actor_daily)"
echo "    fct_cost_actor_daily total after truncate: $actor_after micros"

echo "    4. Re-running dbt incrementally..."
if ! dbt_run_models >/tmp/dbt-run-actor-2.log 2>&1; then
  echo "  FAIL: dbt run failed on second run (see /tmp/dbt-run-actor-2.log)"
  tail -30 /tmp/dbt-run-actor-2.log
  exit 1
fi

final_daily="$(sum_cost fct_cost_daily)"
final_actor="$(sum_cost fct_cost_actor_daily)"
echo "    Final fct_cost_daily total: ${final_daily:-N/A} micros"
echo "    Final fct_cost_actor_daily total: ${final_actor:-N/A} micros"

if [ "$final_daily" = "$initial_daily" ]; then
  echo "  ok   fct_cost_daily: permanent total unchanged after actor rows expired ($initial_daily micros)"
  exit 0
fi

echo "  FAIL: fct_cost_daily permanent total changed from $initial_daily to $final_daily after actor expiry"
exit 1
