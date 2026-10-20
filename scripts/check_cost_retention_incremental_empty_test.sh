#!/usr/bin/env bash
# Check 7: incremental regression with empty fact tables.
# Create/keep the physical tables, empty them, run dbt ONCE without
# --full-refresh, and require
#   sum(fct_cost_daily.cost_usd_micros)
#   = sum(fct_cost_actor_daily.cost_usd_micros)
#   = sum(int_api_request_dedup.cost_usd_micros)
# A zero int baseline is FAIL. Unequal facts vs int is FAIL (today: Modelo's
# empty-table incremental lookback). This script does not change the models.

set -euo pipefail

# shellcheck source=check_cost_retention_lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_cost_retention_lib.sh"

TEST_DAY="$(yesterday_utc)"
TEST_ACTOR="sha256:v1:test-actor-emptyinc-$(date +%s)"
TEST_PREFIX="emptyinc-req-$(date +%s)"

echo "  Running incremental empty-table regression..."

echo "    1. Inserting test raw data for $TEST_DAY..."
if ! insert_test_raw "$TEST_DAY" "$TEST_ACTOR" "$TEST_PREFIX"; then
  echo "  FAIL: could not insert test raw rows into otel.otel_logs"
  exit 1
fi

echo "    2. Ensuring models exist, then emptying the physical fact tables..."
if ! dbt_seed >/tmp/dbt-seed-emptyinc.log 2>&1; then
  echo "  FAIL: dbt seed failed (see /tmp/dbt-seed-emptyinc.log)"
  tail -30 /tmp/dbt-seed-emptyinc.log
  exit 1
fi
# Create tables + populate int (and facts) so int has a non-zero baseline.
if ! dbt_run_models --full-refresh >/tmp/dbt-run-emptyinc-setup.log 2>&1; then
  echo "  FAIL: dbt setup --full-refresh failed (see /tmp/dbt-run-emptyinc-setup.log)"
  tail -30 /tmp/dbt-run-emptyinc-setup.log
  exit 1
fi
if ! table_exists fct_cost_daily || ! table_exists fct_cost_actor_daily; then
  echo "  FAIL: fct_cost_daily or fct_cost_actor_daily missing"
  exit 1
fi

ch_q "TRUNCATE TABLE otel.fct_cost_daily" >/dev/null
ch_q "TRUNCATE TABLE otel.fct_cost_actor_daily" >/dev/null
empty_daily="$(sum_cost fct_cost_daily)"
empty_actor="$(sum_cost fct_cost_actor_daily)"
echo "    Facts emptied: fct_cost_daily=$empty_daily fct_cost_actor_daily=$empty_actor"

echo "    3. dbt run WITHOUT --full-refresh (int_api_request_dedup+)..."
if ! dbt_run_models >/tmp/dbt-run-emptyinc.log 2>&1; then
  echo "  FAIL: dbt run (no full-refresh) failed (see /tmp/dbt-run-emptyinc.log)"
  tail -30 /tmp/dbt-run-emptyinc.log
  exit 1
fi

int_sum="$(sum_cost_int)"
daily_sum="$(sum_cost fct_cost_daily)"
actor_sum="$(sum_cost fct_cost_actor_daily)"
echo "    int_api_request_dedup sum: ${int_sum:-N/A} micros"
echo "    fct_cost_daily sum:        ${daily_sum:-N/A} micros"
echo "    fct_cost_actor_daily sum:  ${actor_sum:-N/A} micros"

if [ -z "${int_sum:-}" ] || [ "$int_sum" = "0" ]; then
  echo "  FAIL: int_api_request_dedup sum(cost_usd_micros) is ${int_sum:-empty} (need a non-zero baseline to validate incremental)"
  exit 1
fi

if [ "$daily_sum" = "$int_sum" ] && [ "$actor_sum" = "$int_sum" ]; then
  echo "  ok   incremental on empty facts reproduced int_api_request_dedup ($int_sum micros)"
  exit 0
fi

echo "  FAIL: empty-table incremental did not reproduce int_api_request_dedup (int=$int_sum daily=$daily_sum actor=$actor_sum)"
exit 1
