#!/usr/bin/env bash
# Expiry safety test for cost aggregates (#45, #47, check 4).
# After raw rows for a day are deleted, a new incremental dbt run must NOT
# change that day's fct_cost_daily total (sum of cost_usd_micros).
#
# First dbt run uses --full-refresh so the initial total cannot be zero just
# because the models already existed empty. A zero initial total, or an empty
# fct_cost_actor_daily, is always FAIL — never PASS comparing empty with empty.

set -euo pipefail

# shellcheck source=check_cost_retention_lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_cost_retention_lib.sh"

TEST_DAY="$(test_day)"
TEST_ACTOR="sha256:v1:test-actor-expiry-$(date +%s)"
TEST_PREFIX="expiry-test-req-$(date +%s)"

echo "  Running expiry safety test with real dbt models..."
echo "    test day (today() - 1): $TEST_DAY"

ch_q "DELETE FROM otel.otel_logs WHERE Body = 'claude_code.api_request' AND (LogAttributes['request_id'] LIKE 'expiry-test-req%' OR LogAttributes['user.email'] LIKE '%test-actor-expiry%')" >/dev/null 2>&1 || true
sleep 1

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

echo "    2. Loading dbt seeds and running models (--full-refresh, int_api_request_dedup+)..."
if ! dbt_seed >/tmp/dbt-seed-1.log 2>&1; then
  echo "  FAIL: dbt seed failed (see /tmp/dbt-seed-1.log)"
  tail -30 /tmp/dbt-seed-1.log
  exit 1
fi
if ! dbt_run_models --full-refresh >/tmp/dbt-run-1.log 2>&1; then
  echo "  FAIL: dbt run --full-refresh failed (see /tmp/dbt-run-1.log)"
  tail -30 /tmp/dbt-run-1.log
  exit 1
fi
echo "    dbt seed + run --full-refresh completed (initial run)"

if ! table_exists fct_cost_daily; then
  echo "  FAIL: fct_cost_daily not created"
  exit 1
fi

initial_daily="$(sum_cost fct_cost_daily "$TEST_DAY")"
initial_actor="0"
actor_rows="0"
if table_exists fct_cost_actor_daily; then
  initial_actor="$(sum_cost fct_cost_actor_daily "$TEST_DAY")"
  actor_rows="$(actor_row_count "$TEST_DAY")"
fi
if ! require_nonzero_reference "$initial_daily" "$initial_actor" "$actor_rows" "check 4 initial $TEST_DAY"; then
  exit 1
fi

echo "    3. Deleting raw data (simulating TTL expiry)..."
ch_q "ALTER TABLE otel.otel_logs DELETE WHERE LogAttributes['request_id'] LIKE '${TEST_PREFIX}%'" >/dev/null
sleep 2
raw_after="$(ch_q "SELECT count() FROM otel.otel_logs WHERE LogAttributes['request_id'] LIKE '${TEST_PREFIX}%' FORMAT TSV")"
echo "    Raw logs after deletion: $raw_after rows"

echo "    4. Re-running dbt incrementally after raw data expired..."
if ! dbt_run_models >/tmp/dbt-run-2.log 2>&1; then
  echo "  FAIL: dbt run failed on second run (see /tmp/dbt-run-2.log)"
  tail -30 /tmp/dbt-run-2.log
  exit 1
fi
echo "    dbt run completed (second run, raw data gone)"

final_daily="$(sum_cost fct_cost_daily "$TEST_DAY")"
final_actor="$(sum_cost fct_cost_actor_daily "$TEST_DAY")"
final_actor_rows="$(actor_row_count "$TEST_DAY")"
echo "    totals (check 4 final $TEST_DAY): fct_cost_daily=${final_daily:-empty} micros  fct_cost_actor_daily=${final_actor:-empty} micros  actor_rows=${final_actor_rows:-empty}"

if [ "$final_daily" = "$initial_daily" ]; then
  echo "  ok   fct_cost_daily: day_utc total unchanged after raw data expired ($initial_daily micros)"
  exit 0
fi

echo "  FAIL: fct_cost_daily day_utc total changed from $initial_daily to $final_daily"
exit 1
