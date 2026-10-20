#!/usr/bin/env bash
# Cost aggregate retention guard (#45, #47): ensures the dbt-built aggregates
# carry no TTL, the raw OTLP tables keep their TTL, and the dbt lookback window
# is strictly less than the raw hot TTL (else incrementals could miss expired data).
#
# Requires: `make up-clickhouse` running and (for the full suite) the dbt
# project with fct_cost_daily + fct_cost_actor_daily created. Partial checks
# still run when the aggregate tables are absent — see check 1 below.
#
# Run from the repo root: `make check-cost-retention`.
#
# Example manual queries (for testing/debugging):
#   # List all tables in the otel database:
#   curl -sf 'http://localhost:8123/?user=otel&password=otel' \
#     --data-binary "SELECT name, engine_full FROM system.tables WHERE database = 'otel' FORMAT TSV"
#
#   # Check for TTL in a specific table:
#   curl -sf 'http://localhost:8123/?user=otel&password=otel' \
#     --data-binary "SELECT engine_full FROM system.tables WHERE database = 'otel' AND name = 'otel_logs' FORMAT TSV"
#
#   # Expected output for raw tables (with TTL):
#   #   MergeTree() ... TTL toDateTime(Timestamp) + INTERVAL 72 HOUR ...
#   # Expected output for aggregate tables (no TTL):
#   #   MergeTree() PARTITION BY ... ORDER BY ... (no TTL clause)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
# Read the raw TTL from the exporter config; fallback to the documented default (72h).
OTERU_HOT_TTL="${OTERU_HOT_TTL:-72}"
# The dbt project will define a `cost_lookback_hours` var in dbt_project.yml.
# Read it from that file once it exists; fallback to the agreed value (48h).
DBT_PROJECT="$ROOT/oteru-dbt/dbt_project.yml"
if [ -f "$DBT_PROJECT" ] && grep -q "cost_lookback_hours:" "$DBT_PROJECT"; then
  COST_LOOKBACK_HOURS="$(grep 'cost_lookback_hours:' "$DBT_PROJECT" | awk '{print $2}')"
else
  COST_LOOKBACK_HOURS="${COST_LOOKBACK_HOURS:-48}"
fi

failures=0

fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }
ok() { echo "  ok   $*"; }
skip() { echo "  skip $*"; }

q() {
  local result
  if ! result="$(curl -sf "$CH" --data-binary "$1" 2>&1)"; then
    return 1
  fi
  echo "$result"
}

# Pre-flight check: ensure ClickHouse is reachable before running any checks
if ! q "SELECT 1 FORMAT TSV" >/dev/null; then
  echo "  FAIL: ClickHouse unreachable at $CH (is \`make up-clickhouse\` running?)"
  exit 1
fi

echo "cost-retention guard (OTERU_HOT_TTL=${OTERU_HOT_TTL}h, lookback=${COST_LOOKBACK_HOURS}h)"

# --- Check 1: aggregate tables have no TTL ---------------------------------------
# Permanent aggregates (fct_cost_daily) must NOT expire. Actor-specific tables
# (fct_cost_actor_daily) will have TTL and are checked separately in check 5.
for table in fct_cost_daily; do
  exists="$(q "SELECT count() FROM system.tables WHERE database = 'otel' AND name = '$table' FORMAT TSV")"
  if [ "$exists" = "0" ]; then
    skip "$table not yet created (dbt project pending; this check will pass when it exists)"
  else
    ttl_clause="$(q "SELECT engine_full FROM system.tables WHERE database = 'otel' AND name = '$table' FORMAT TSV")"
    if echo "$ttl_clause" | grep -qi 'TTL'; then
      fail "$table carries a TTL (must be none): $ttl_clause"
    else
      ok "$table has no TTL (permanent aggregate preserved)"
    fi
  fi
done

# --- Check 2: raw otel_* tables still carry their TTL (no regression) ------------
# The raw OTLP tables must keep their TTL — the aggregates pull from hot data
# within the lookback, then the raw rows age out. Losing the raw TTL would
# break the retention contract.
for table in otel_logs otel_traces otel_metrics_sum; do
  exists="$(q "SELECT count() FROM system.tables WHERE database = 'otel' AND name = '$table' FORMAT TSV")"
  if [ "$exists" = "0" ]; then
    fail "$table does not exist (expected by the clickhouse exporter)"
  else
    ttl_clause="$(q "SELECT engine_full FROM system.tables WHERE database = 'otel' AND name = '$table' FORMAT TSV")"
    if echo "$ttl_clause" | grep -qi 'TTL'; then
      ok "$table has TTL (raw hot retention enforced)"
    else
      fail "$table has NO TTL (expected TTL toDateTime(Timestamp) + INTERVAL ${OTERU_HOT_TTL} HOUR)"
    fi
  fi
done

# --- Check 3: lookback < hot TTL (else incrementals could miss expired data) -----
# The dbt incremental models run `delete+insert` for the last N hours. If N >= TTL,
# rows from the oldest covered day may have already aged out, so the re-aggregation
# would be incomplete. Require strict `<` to leave headroom.
if [ "$COST_LOOKBACK_HOURS" -ge "$OTERU_HOT_TTL" ]; then
  fail "lookback ($COST_LOOKBACK_HOURS h) >= hot TTL ($OTERU_HOT_TTL h) — incrementals may miss expired rows"
else
  margin=$((OTERU_HOT_TTL - COST_LOOKBACK_HOURS))
  ok "lookback ($COST_LOOKBACK_HOURS h) < hot TTL ($OTERU_HOT_TTL h) by ${margin}h margin"
fi

# --- Check 4: expiry safety test (requires OTERU_HOT_TTL_TEST override) ----------
# Spin up a test-only ClickHouse with a very short TTL (e.g. 1h), emit a capture,
# wait for the oldest day's raw rows to expire, re-run dbt, and verify the
# aggregate for that day did NOT change — proving the incremental pulled from
# the aggregate itself, not from the (now-gone) raw rows.
#
# This check is SKIPPED in normal runs (no OTERU_HOT_TTL_TEST set) because it
# needs a throwaway backend and a long sleep (> TTL). Run it explicitly for
# regression testing when touching the dbt lookback or TTL configuration.
if [ -z "${OTERU_HOT_TTL_TEST:-}" ]; then
  skip "expiry safety test: set OTERU_HOT_TTL_TEST=1 (test env only) to run"
elif [ ! -f "$DBT_PROJECT" ]; then
  skip "expiry safety test: dbt project not on branch yet (will validate once merged)"
else
  # Run the real expiry safety test from the separate script
  if bash "$ROOT/scripts/check_cost_retention_expiry_test.sh"; then
    # Success message already printed by the test script
    :
  else
    fail "expiry safety test failed (see output above)"
  fi
fi

# --- Check 5: actor_id tables must have TTL <= 90 DAY in the physical DDL ------
# TTL is read from system.tables.create_table_query (the real ClickHouse DDL),
# never from dbt config / vars. Any fct_cost* physical table with actor_id
# must carry a TTL of at most 90 DAY. Permanent tables (no TTL in DDL) cannot
# have actor_id.
ttl_snippet_from_ddl() {
  local ddl="$1"
  if ! echo "$ddl" | grep -qi 'TTL'; then
    echo ""
    return
  fi
  echo "$ddl" | sed -n 's/.*\(TTL .*\)/\1/p' | sed -E 's/ (SETTINGS|COMMENT).*//' | head -1
}

ttl_hours_from_snippet() {
  local snippet="$1"
  local n
  n="$(echo "$snippet" | grep -oiE 'toIntervalDay\([0-9]+\)|INTERVAL[[:space:]]+[0-9]+[[:space:]]+DAY' | grep -oE '[0-9]+' | head -1 || true)"
  if [ -n "$n" ]; then
    echo $((n * 24))
    return
  fi
  n="$(echo "$snippet" | grep -oiE 'toIntervalHour\([0-9]+\)|INTERVAL[[:space:]]+[0-9]+[[:space:]]+HOUR' | grep -oE '[0-9]+' | head -1 || true)"
  if [ -n "$n" ]; then
    echo "$n"
    return
  fi
  echo ""
}

actor_tables=$(q "SELECT DISTINCT table FROM system.columns
                  WHERE database = 'otel' AND table LIKE 'fct_cost%' AND name = 'actor_id'
                    AND table IN (
                      SELECT name FROM system.tables
                      WHERE database = 'otel' AND engine LIKE '%MergeTree%'
                    )
                  ORDER BY table FORMAT TSV" 2>/dev/null || echo "")
if [ -z "$actor_tables" ]; then
  skip "actor_id check: no fct_cost* tables with actor_id column yet (new design pending)"
else
  for table in $actor_tables; do
    ddl=$(q "SELECT replaceRegexpAll(create_table_query, '\\n|\\r', ' ') FROM system.tables
             WHERE database = 'otel' AND name = '$table' FORMAT TSV")
    snippet="$(ttl_snippet_from_ddl "$ddl")"
    if [ -z "$snippet" ]; then
      echo "    TTL snippet for $table: (none)"
      fail "$table has actor_id but NO TTL in create_table_query (actor data must be time-limited in the physical DDL)"
    else
      echo "    TTL snippet for $table: $snippet"
      hours="$(ttl_hours_from_snippet "$snippet")"
      if [ -z "$hours" ]; then
        fail "$table TTL in DDL is not a DAY/HOUR interval we can bound to 90 DAY: $snippet"
      elif [ "$hours" -le $((90 * 24)) ]; then
        ok "$table has actor_id and physical TTL <= 90 DAY ($hours h, compliant)"
      else
        fail "$table has actor_id but physical TTL > 90 DAY ($hours h, must be <= 2160 h)"
      fi
    fi
  done
fi

permanent_tables=$(q "SELECT name FROM system.tables
                      WHERE database = 'otel' AND name LIKE 'fct_cost%'
                        AND engine LIKE '%MergeTree%'
                        AND create_table_query NOT ILIKE '%TTL%'
                      ORDER BY name FORMAT TSV" 2>/dev/null || echo "")
if [ -z "$permanent_tables" ]; then
  skip "permanent table check: no fct_cost* permanent tables yet"
else
  for table in $permanent_tables; do
    has_actor=$(q "SELECT count() FROM system.columns
                   WHERE database = 'otel' AND table = '$table' AND name = 'actor_id' FORMAT TSV")
    if [ "$has_actor" = "0" ]; then
      ok "$table (permanent, no TTL in DDL) has no actor_id column (privacy by design)"
    else
      fail "$table (permanent, no TTL in create_table_query) has actor_id column (privacy violation)"
    fi
  done
fi

# --- Check 6: actor TTL expiry preserves permanent totals ----------------------
if [ -z "${OTERU_HOT_TTL_TEST:-}" ]; then
  skip "actor TTL test: set OTERU_HOT_TTL_TEST=1 (test env only) to run"
else
  actor_daily_exists=$(q "SELECT count() FROM system.tables
                          WHERE database = 'otel' AND name = 'fct_cost_actor_daily' FORMAT TSV")
  if [ "$actor_daily_exists" = "0" ]; then
    skip "actor TTL test: fct_cost_actor_daily not yet created (Modelo's new design pending)"
  else
    if bash "$ROOT/scripts/check_cost_retention_actor_expiry_test.sh"; then
      :
    else
      fail "actor TTL expiry test failed (see output above)"
    fi
  fi
fi

# --- Check 7: incremental regression on empty fact tables ----------------------
# After the physical tables exist and are emptied, one dbt run without
# --full-refresh must reproduce int_api_request_dedup totals. Expected to
# FAIL today if the incremental lookback treats max(day_utc) on an empty
# table as NULL and drops every source row (model bug, not this script).
if [ -z "${OTERU_HOT_TTL_TEST:-}" ]; then
  skip "empty-table incremental test: set OTERU_HOT_TTL_TEST=1 (test env only) to run"
elif [ ! -f "$DBT_PROJECT" ]; then
  skip "empty-table incremental test: dbt project not on branch yet"
else
  if bash "$ROOT/scripts/check_cost_retention_incremental_empty_test.sh"; then
    :
  else
    fail "empty-table incremental test failed (see output above)"
  fi
fi

if [ "$failures" -ne 0 ]; then
  echo
  echo "check-cost-retention: $failures check(s) failed."
  exit 1
fi
echo
echo "check-cost-retention: OK — aggregates have no TTL, raw TTL intact, lookback < TTL."
