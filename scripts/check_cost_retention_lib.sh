#!/usr/bin/env bash
# Shared helpers for cost-retention guard scripts (#45, #47).
# Sourced by check_cost_retention_*.sh — not invoked directly.

CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DBT_DIR="$ROOT/oteru-dbt"
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

# Resolve the dbt CLI: $DBT_BIN, then oteru-dbt's venv, then PATH.
resolve_dbt() {
  if [ -n "${DBT_BIN:-}" ]; then
    if [ -x "$DBT_BIN" ]; then
      echo "$DBT_BIN"
      return 0
    fi
    if command -v "$DBT_BIN" >/dev/null 2>&1; then
      command -v "$DBT_BIN"
      return 0
    fi
    echo "  FAIL: DBT_BIN=$DBT_BIN is set but not an executable dbt" >&2
    return 1
  fi
  local candidate
  for candidate in \
    "$ROOT/oteru-dbt/.venv/bin/dbt" \
    "$ROOT/oteru-dbt/venv/bin/dbt"
  do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  if command -v dbt >/dev/null 2>&1; then
    command -v dbt
    return 0
  fi
  echo "  FAIL: dbt not found. Set DBT_BIN to the dbt executable, install dbt in oteru-dbt/.venv, or put dbt on PATH." >&2
  return 1
}

if ! DBT="$(resolve_dbt)"; then
  exit 1
fi

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

actor_row_count() {
  local day="${1:-}"
  if [ -n "$day" ]; then
    ch_q "SELECT count() FROM otel.fct_cost_actor_daily WHERE day_utc = '$day' FORMAT TSV"
  else
    ch_q "SELECT count() FROM otel.fct_cost_actor_daily FORMAT TSV"
  fi
}

# ClickHouse-relative test day (today() - 1), so rows stay inside the 90-day actor TTL.
test_day() {
  ch_q "SELECT toString(today() - 1) FORMAT TSV"
}

# Synthetic actor_id: hmac:v1: + 64 lowercase hex. Not a real HMAC (no key).
# $1 is a short tag mixed into the last 16 hex chars so runs do not collide.
fake_hmac_actor() {
  local tag="${1:-test}"
  local mix
  mix="$(printf '%s-%s' "$tag" "$(date +%s%N)" | sha256sum | awk '{print $1}')"
  printf 'hmac:v1:%s\n' "$mix"
}

# Populate a known otel_logs column if DESCRIBE reports it. Dates are now()-relative.
_expr_for_otel_logs_col() {
  local col="$1"
  case "$col" in
    Timestamp|TimestampTime)
      echo "toDateTime64(toStartOfDay(now() - INTERVAL 1 DAY) + toIntervalHour(_hour), 9)"
      ;;
    TimestampNs)
      echo "toUInt64(toUnixTimestamp(toStartOfDay(now() - INTERVAL 1 DAY) + toIntervalHour(_hour))) * 1000000000"
      ;;
    Body)
      echo "'claude_code.api_request'"
      ;;
    ServiceName)
      echo "'claude-code'"
      ;;
    ResourceAttributes)
      echo "map('oteru.traffic', 'test')"
      ;;
    LogAttributes)
      echo "map('request_id', concat('${_prefix}', '-', leftPad(toString(_n), 3, '0')), 'session.id', concat('${_prefix}', '-session'), 'model', '$TEST_MODEL', 'cost_usd', if(_n = 1, '0.015', '0.025'), 'input_tokens', if(_n = 1, '1000', '2000'), 'output_tokens', if(_n = 1, '500', '1000'), 'cache_read_tokens', '0', 'cache_creation_tokens', '0', 'user.email', '${_actor}')"
      ;;
    *)
      echo ""
      ;;
  esac
}

# INSERT ... SELECT using the real column names from DESCRIBE / system.columns.
# Never uses a positional VALUES list (exporter schema has more than 6 columns).
insert_test_raw() {
  local actor="$1"
  local prefix="$2"
  local cols insert_cols select_exprs col expr
  cols="$(ch_q "SELECT name FROM system.columns WHERE database = 'otel' AND table = 'otel_logs' ORDER BY position FORMAT TSV")" || return 1
  if [ -z "$cols" ]; then
    echo "  FAIL: DESCRIBE otel.otel_logs returned no columns"
    return 1
  fi

  _actor="$actor"
  _prefix="$prefix"
  insert_cols=""
  select_exprs=""
  while IFS= read -r col; do
    [ -z "$col" ] && continue
    expr="$(_expr_for_otel_logs_col "$col")"
    [ -z "$expr" ] && continue
    if [ -n "$insert_cols" ]; then
      insert_cols+=", "
      select_exprs+=", "
    fi
    insert_cols+="\`${col}\`"
    select_exprs+="$expr"
  done <<< "$cols"
  unset _actor _prefix

  if [ -z "$insert_cols" ]; then
    echo "  FAIL: otel.otel_logs has none of the expected exporter columns (Timestamp/Body/ServiceName/ResourceAttributes/LogAttributes)"
    return 1
  fi

  ch_q "INSERT INTO otel.otel_logs (${insert_cols})
        SELECT ${select_exprs}
        FROM (
          SELECT 10 AS _hour, 1 AS _n
          UNION ALL
          SELECT 14 AS _hour, 2 AS _n
        )"
}

# Fail unless the permanent total is > 0 and fct_cost_actor_daily has rows + a
# positive sum. Never allow a later PASS that compared empty with empty.
require_nonzero_reference() {
  local daily="$1"
  local actor_sum="$2"
  local actor_rows="$3"
  local label="$4"

  echo "    totals ($label): fct_cost_daily=${daily:-empty} micros  fct_cost_actor_daily=${actor_sum:-empty} micros  actor_rows=${actor_rows:-empty}"

  if ! table_exists fct_cost_actor_daily; then
    echo "  FAIL: fct_cost_actor_daily does not exist (need at least one actor row before comparing)"
    return 1
  fi
  if [ -z "${actor_rows:-}" ] || [ "$actor_rows" = "0" ]; then
    echo "  FAIL: fct_cost_actor_daily is empty (0 rows) at $label — never PASS comparing empty with empty"
    return 1
  fi
  if [ -z "${daily:-}" ] || [ "$daily" = "0" ]; then
    echo "  FAIL: fct_cost_daily reference total is ${daily:-empty} at $label (must be > 0)"
    return 1
  fi
  if [ -z "${actor_sum:-}" ] || [ "$actor_sum" = "0" ]; then
    echo "  FAIL: fct_cost_actor_daily reference total is ${actor_sum:-empty} at $label (must be > 0)"
    return 1
  fi
  return 0
}

dbt_seed() {
  (cd "$DBT_DIR" && "$DBT" seed --profiles-dir .)
}

# Always selects int_api_request_dedup+ so both physical facts are covered.
dbt_run_models() {
  (cd "$DBT_DIR" && "$DBT" run --select int_api_request_dedup+ --profiles-dir . "$@")
}
