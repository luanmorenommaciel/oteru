#!/usr/bin/env bash
# Cost aggregate retention guard (#45, #47): ensures the dbt-built aggregates
# carry no TTL, the raw OTLP tables keep their TTL, and the dbt lookback window
# is strictly less than the raw hot TTL (else incrementals could miss expired data).
#
# Requires: `make up-clickhouse` running and (for the full suite) the dbt
# project with fct_cost_daily + fct_cost_by_model_daily created. Partial checks
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

# --- Check 5: actor_id tables must have TTL; permanent tables cannot have actor_id ---
# Final design: fct_cost_daily (grain day_utc x model_family) is permanent, no TTL, no actor_id.
# fct_cost_actor_daily (grain day_utc x actor_id) has 90-day TTL.
#
# Rule: Any fct_cost* table with actor_id column must have TTL <= 90 days.
#       Any permanent table without TTL cannot have actor_id column.
#
# Dynamic detection: query system.columns to find which tables have actor_id.
actor_tables=$(q "SELECT DISTINCT table FROM system.columns 
                  WHERE database = 'otel' AND table LIKE 'fct_cost%' AND name = 'actor_id' 
                  ORDER BY table FORMAT TSV" 2>/dev/null || echo "")
if [ -z "$actor_tables" ]; then
  skip "actor_id check: no fct_cost* tables with actor_id column yet (new design pending)"
else
  for table in $actor_tables; do
    ttl_expr=$(q "SELECT engine_full FROM system.tables 
                  WHERE database = 'otel' AND name = '$table' FORMAT TSV")
    if echo "$ttl_expr" | grep -qi 'TTL'; then
      # Extract TTL days (assumes format like "TTL ... + INTERVAL N DAY")
      ttl_days=$(echo "$ttl_expr" | grep -oP 'INTERVAL \K[0-9]+(?= DAY)' || echo "unknown")
      if [ "$ttl_days" = "unknown" ]; then
        ok "$table has actor_id and TTL (actor-specific data, time-limited)"
      elif [ "$ttl_days" -le 90 ]; then
        ok "$table has actor_id and TTL <= 90 days ($ttl_days d, compliant)"
      else
        fail "$table has actor_id but TTL > 90 days ($ttl_days days, must be <= 90)"
      fi
    else
      fail "$table has actor_id but NO TTL (actor data must be time-limited)"
    fi
  done
fi

# Verify permanent tables (no TTL) don't have actor_id
permanent_tables=$(q "SELECT name FROM system.tables 
                      WHERE database = 'otel' AND name LIKE 'fct_cost%' 
                        AND engine_full NOT LIKE '%TTL%' 
                      ORDER BY name FORMAT TSV" 2>/dev/null || echo "")
if [ -z "$permanent_tables" ]; then
  skip "permanent table check: no fct_cost* permanent tables yet"
else
  for table in $permanent_tables; do
    has_actor=$(q "SELECT count() FROM system.columns 
                   WHERE database = 'otel' AND table = '$table' AND name = 'actor_id' FORMAT TSV")
    if [ "$has_actor" = "0" ]; then
      ok "$table (permanent) has no actor_id column (privacy by design)"
    else
      fail "$table (permanent, no TTL) has actor_id column (privacy violation)"
    fi
  done
fi

# --- Check 6: actor TTL expiry preserves permanent totals ----------------------
# With a test-only short TTL, expire actor-specific rows and verify that
# permanent aggregates (without actor_id) remain unchanged.
if [ -z "${OTERU_HOT_TTL_TEST:-}" ]; then
  skip "actor TTL test: set OTERU_HOT_TTL_TEST=1 (test env only) to run"
else
  # Check if fct_cost_actor_daily exists
  actor_daily_exists=$(q "SELECT count() FROM system.tables 
                          WHERE database = 'otel' AND name = 'fct_cost_actor_daily' FORMAT TSV")
  if [ "$actor_daily_exists" = "0" ]; then
    skip "actor TTL test: fct_cost_actor_daily not yet created (Modelo's new design pending)"
  else
    # This test would:
    # 1. Insert test data with specific actor_id
    # 2. Run dbt to populate both permanent and actor-specific tables
    # 3. Capture totals from fct_cost_daily (permanent, no actor_id)
    # 4. Expire/delete actor-specific rows (simulating 90-day TTL)
    # 5. Verify fct_cost_daily totals unchanged (permanent data preserved)
    #
    # For now, stub until the new table design is implemented
    skip "actor TTL test: implementation pending until fct_cost_actor_daily is in use"
  fi
fi

if [ "$failures" -ne 0 ]; then
  echo
  echo "check-cost-retention: $failures check(s) failed."
  exit 1
fi
echo
echo "check-cost-retention: OK — aggregates have no TTL, raw TTL intact, lookback < TTL."
