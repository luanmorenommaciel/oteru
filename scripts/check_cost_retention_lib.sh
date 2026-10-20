#!/usr/bin/env bash
# Shared helpers for cost-retention guard scripts (#45, #47).
# Sourced by check_cost_retention_*.sh — not invoked directly.

CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DBT_DIR="$ROOT/oteru-dbt"
VENV_PYTHON="$ROOT/oteru-emitter/.venv/bin/python"
TEST_MODEL="${TEST_MODEL:-claude-3-5-sonnet-20241022}"

export CLICKHOUSE_HOST="${CLICKHOUSE_HOST:-localhost}"
export CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
export CLICKHOUSE_USER="${CLICKHOUSE_USER:-otel}"
export CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-otel}"

ch_q() {
  curl -sf "$CH" --data-binary "$1" 2>&1 || {
    echo "  FAIL: Query failed: $1"
    return 1
  }
}

table_exists() {
  local name="$1"
  local n
  n="$(ch_q "SELECT count() FROM system.tables WHERE database = 'otel' AND name = '$name' FORMAT TSV" 2>/dev/null || echo 0)"
  [ "$n" = "1" ]
}

sum_cost() {
  local table="$1"
  local day="${2:-}"
  local sql
  if [ -n "$day" ]; then
    sql="SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.$table WHERE day_utc = '$day' FORMAT TSV"
  else
    sql="SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.$table FORMAT TSV"
  fi
  ch_q "$sql"
}

sum_cost_int() {
  ch_q "SELECT coalesce(sum(cost_usd_micros), 0) FROM otel.int_api_request_dedup FORMAT TSV"
}

insert_test_raw() {
  local day="$1"
  local actor="$2"
  local prefix="$3"
  local ts1 ts2
  ts1="$(date -d "$day 10:00:00" +%s 2>/dev/null || echo 1729418400)"
  ts2="$(date -d "$day 14:00:00" +%s 2>/dev/null || echo 1729432800)"
  ch_q "INSERT INTO otel.otel_logs (Timestamp, Body, ServiceName, ResourceAttributes, LogAttributes) VALUES
    (toDateTime64('$day 10:00:00', 9), 'claude_code.api_request', 'claude-code',
     {'oteru.traffic': 'test'},
     {'request_id': '${prefix}-001', 'session.id': '${prefix}-session', 'model': '$TEST_MODEL',
      'cost_usd': '0.015', 'input_tokens': '1000', 'output_tokens': '500',
      'cache_read_tokens': '0', 'cache_creation_tokens': '0', 'user.email': '$actor',
      'timeUnixNano': '${ts1}000000000'}),
    (toDateTime64('$day 14:00:00', 9), 'claude_code.api_request', 'claude-code',
     {'oteru.traffic': 'test'},
     {'request_id': '${prefix}-002', 'session.id': '${prefix}-session', 'model': '$TEST_MODEL',
      'cost_usd': '0.025', 'input_tokens': '2000', 'output_tokens': '1000',
      'cache_read_tokens': '0', 'cache_creation_tokens': '0', 'user.email': '$actor',
      'timeUnixNano': '${ts2}000000000'})"
}

dbt_seed() {
  (cd "$DBT_DIR" && "$VENV_PYTHON" -m dbt.cli.main seed --profiles-dir .)
}

# Usage: dbt_run_models [extra dbt args...]
# Always selects int_api_request_dedup+ so both physical facts are covered.
dbt_run_models() {
  (cd "$DBT_DIR" && "$VENV_PYTHON" -m dbt.cli.main run --select int_api_request_dedup+ --profiles-dir . "$@")
}

yesterday_utc() {
  date -u -d '1 day ago' '+%Y-%m-%d' 2>/dev/null || date -u -v-1d '+%Y-%m-%d' 2>/dev/null || echo '2026-10-19'
}
