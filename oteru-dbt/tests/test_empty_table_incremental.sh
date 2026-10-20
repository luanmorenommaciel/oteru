#!/usr/bin/env bash
# Regression: incremental run against an existing EMPTY fact table must
# still load every int_api_request_dedup row.
#
# Fails on the old lookback (`max(day_utc) - INTERVAL 2 DAY` is NULL on
# an empty table and the WHERE drops every source row).
#
# Local disposable ClickHouse only. Assumes models already exist and
# int_api_request_dedup is populated.
#
# Usage (from repo root, ClickHouse already running):
#   bash oteru-dbt/tests/test_empty_table_incremental.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Prefer the same default-user HTTP as a local binary ClickHouse; fall
# back to the collector user if CLICKHOUSE_URL is already set.
CH="${CLICKHOUSE_URL:-http://localhost:8123/}"
DBT_DIR="$ROOT/oteru-dbt"
DBT_BIN="${DBT_BIN:-dbt}"

q() { curl -sf "$CH" --data-binary "$1"; }

echo "empty-table incremental regression (local ClickHouse only)"

int_sum="$(q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.int_api_request_dedup FORMAT TSV")"
if [ -z "${int_sum:-}" ] || [ "$int_sum" = "0" ]; then
  echo "  FAIL: int_api_request_dedup sum is ${int_sum:-empty} (need a non-zero baseline)"
  exit 1
fi
echo "  int_api_request_dedup baseline: $int_sum micros"

echo "  truncating fct_cost_daily and fct_cost_actor_daily..."
q "TRUNCATE TABLE otel.fct_cost_daily" >/dev/null
q "TRUNCATE TABLE otel.fct_cost_actor_daily" >/dev/null

empty_daily="$(q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.fct_cost_daily FORMAT TSV")"
empty_actor="$(q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.fct_cost_actor_daily FORMAT TSV")"
echo "  after truncate: daily=$empty_daily actor=$empty_actor"

echo "  dbt run --select fct_cost_daily fct_cost_actor_daily (no --full-refresh)..."
if ! (cd "$DBT_DIR" && "$DBT_BIN" run --profiles-dir . --select fct_cost_daily fct_cost_actor_daily >/tmp/dbt-empty-incr-regression.log 2>&1); then
  echo "  FAIL: dbt run failed (see /tmp/dbt-empty-incr-regression.log)"
  tail -30 /tmp/dbt-empty-incr-regression.log
  exit 1
fi

daily_sum="$(q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.fct_cost_daily FORMAT TSV")"
actor_sum="$(q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.fct_cost_actor_daily FORMAT TSV")"
echo "  after incremental: daily=$daily_sum actor=$actor_sum int=$int_sum"

if [ "$daily_sum" = "$int_sum" ] && [ "$actor_sum" = "$int_sum" ]; then
  echo "  PASS: empty-table incremental reproduced int_api_request_dedup ($int_sum micros)"
  exit 0
fi

echo "  FAIL: daily=$daily_sum actor=$actor_sum != int=$int_sum"
exit 1
