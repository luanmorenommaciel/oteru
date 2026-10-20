#!/usr/bin/env bash
# Check 7: incremental regression with empty fact tables.
# Create/keep the physical tables, empty them, run dbt ONCE without
# --full-refresh, and require
#   sum(fct_cost_daily.cost_usd_micros)
#   = sum(fct_cost_actor_daily.cost_usd_micros)
#   = sum(int_api_request_dedup.cost_usd_micros)
# Reference totals must be > 0 and fct_cost_actor_daily must have at least
# one row before any comparison. Never PASS comparing empty with empty.

set -euo pipefail

# shellcheck source=check_cost_retention_lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check_cost_retention_lib.sh"

TEST_DAY="$(test_day)"
TEST_ACTOR="$(fake_hmac_actor emptyinc)"
TEST_PREFIX="emptyinc-req-$(date +%s)"

echo "  Running incremental empty-table regression..."
echo "    test day (today() - 1): $TEST_DAY"

echo "    1. Inserting test raw data for $TEST_DAY (INSERT … SELECT, columns from DESCRIBE)..."
if ! insert_test_raw "$TEST_ACTOR" "$TEST_PREFIX"; then
  echo "  FAIL: could not insert test raw rows into otel.otel_logs"
  exit 1
fi

echo "    2. Ensuring models exist (full-refresh), then emptying the physical fact tables..."
if ! dbt_seed >/tmp/dbt-seed-emptyinc.log 2>&1; then
  echo "  FAIL: dbt seed failed (see /tmp/dbt-seed-emptyinc.log)"
  tail -30 /tmp/dbt-seed-emptyinc.log
  exit 1
fi
if ! dbt_run_models --full-refresh >/tmp/dbt-run-emptyinc-setup.log 2>&1; then
  echo "  FAIL: dbt setup --full-refresh failed (see /tmp/dbt-run-emptyinc-setup.log)"
  tail -30 /tmp/dbt-run-emptyinc-setup.log
  exit 1
fi
if ! table_exists fct_cost_daily || ! table_exists fct_cost_actor_daily; then
  echo "  FAIL: fct_cost_daily or fct_cost_actor_daily missing"
  exit 1
fi

setup_daily="$(sum_cost fct_cost_daily)"
setup_actor="$(sum_cost fct_cost_actor_daily)"
setup_actor_rows="$(actor_row_count)"
setup_int="$(sum_cost_int)"
echo "    totals (check 7 after setup): int=${setup_int:-empty} daily=${setup_daily:-empty} actor=${setup_actor:-empty} actor_rows=${setup_actor_rows:-empty}"
if ! require_nonzero_reference "$setup_daily" "$setup_actor" "$setup_actor_rows" "check 7 setup"; then
  exit 1
fi
if [ -z "${setup_int:-}" ] || [ "$setup_int" = "0" ]; then
  echo "  FAIL: int_api_request_dedup reference total is ${setup_int:-empty} after setup (must be > 0)"
  exit 1
fi

ch_q "TRUNCATE TABLE otel.fct_cost_daily" >/dev/null
ch_q "TRUNCATE TABLE otel.fct_cost_actor_daily" >/dev/null
echo "    Facts emptied: fct_cost_daily=$(sum_cost fct_cost_daily) fct_cost_actor_daily=$(sum_cost fct_cost_actor_daily) actor_rows=$(actor_row_count)"

echo "    3. dbt run WITHOUT --full-refresh (int_api_request_dedup+)..."
if ! dbt_run_models >/tmp/dbt-run-emptyinc.log 2>&1; then
  echo "  FAIL: dbt run (no full-refresh) failed (see /tmp/dbt-run-emptyinc.log)"
  tail -30 /tmp/dbt-run-emptyinc.log
  exit 1
fi

int_sum="$(sum_cost_int)"
daily_sum="$(sum_cost fct_cost_daily)"
actor_sum="$(sum_cost fct_cost_actor_daily)"
actor_rows="$(actor_row_count)"
echo "    totals (check 7 after incremental): int=${int_sum:-empty} daily=${daily_sum:-empty} actor=${actor_sum:-empty} actor_rows=${actor_rows:-empty}"

if [ -z "${int_sum:-}" ] || [ "$int_sum" = "0" ]; then
  echo "  FAIL: int_api_request_dedup reference total is ${int_sum:-empty} (must be > 0; never PASS comparing empty with empty)"
  exit 1
fi
if [ -z "${actor_rows:-}" ] || [ "$actor_rows" = "0" ]; then
  echo "  FAIL: fct_cost_actor_daily is empty after incremental (0 rows) — never PASS comparing empty with empty"
  exit 1
fi
if [ -z "${daily_sum:-}" ] || [ "$daily_sum" = "0" ]; then
  echo "  FAIL: fct_cost_daily total is ${daily_sum:-empty} after incremental (must be > 0)"
  exit 1
fi
if [ -z "${actor_sum:-}" ] || [ "$actor_sum" = "0" ]; then
  echo "  FAIL: fct_cost_actor_daily total is ${actor_sum:-empty} after incremental (must be > 0)"
  exit 1
fi

if [ "$daily_sum" = "$int_sum" ] && [ "$actor_sum" = "$int_sum" ]; then
  echo "  ok   incremental on empty facts reproduced int_api_request_dedup ($int_sum micros)"
  exit 0
fi

echo "  FAIL: empty-table incremental did not reproduce int_api_request_dedup (int=$int_sum daily=$daily_sum actor=$actor_sum)"
exit 1
