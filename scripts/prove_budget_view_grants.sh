#!/usr/bin/env bash
# #48 test 2b (live): oteru_budget_alerts_reader allowed/denied + definer grants.
# Requires a local ClickHouse with the budget objects already built.
# Credentials only via env. Never prints a password.
# Exit 0 on pass; 1 on a failed assertion.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="${CLICKHOUSE_URL:-http://127.0.0.1:8123/}"
ADMIN_USER="${CLICKHOUSE_USER:-default}"

_admin() {
  if [[ -n "${CLICKHOUSE_PASSWORD:-}" ]]; then
    curl -sS -f "$CH" \
      -H "X-ClickHouse-User: ${ADMIN_USER}" \
      -H "X-ClickHouse-Key: ${CLICKHOUSE_PASSWORD}" \
      --data-binary "$1"
  else
    curl -sS -f "$CH" \
      -H "X-ClickHouse-User: ${ADMIN_USER}" \
      --data-binary "$1"
  fi
}

_as_role_user() {
  # Temporary local user holding the alerts role. Passwordless HOST LOCAL.
  curl -sS "$CH" \
    -H "X-ClickHouse-User: oteru_budget_alerts_ci" \
    --data-binary "$1" 2>&1 || true
}

bash "$ROOT/scripts/bootstrap_budget_roles.sh" --require
_admin "CREATE USER IF NOT EXISTS oteru_budget_alerts_ci IDENTIFIED WITH no_password HOST LOCAL" >/dev/null
_admin "GRANT oteru_budget_alerts_reader TO oteru_budget_alerts_ci" >/dev/null

fail=0
expect_ok() {
  local label="$1" sql="$2"
  local out
  out="$(_as_role_user "$sql")"
  if [[ "$out" == *ACCESS_DENIED* ]] || [[ "$out" == *Code:\ 497* ]]; then
    echo "FAIL allowed: $label"
    fail=1
  else
    echo "PASS allowed: $label"
  fi
}
expect_denied() {
  local label="$1" sql="$2"
  local out
  out="$(_as_role_user "$sql")"
  if [[ "$out" == *ACCESS_DENIED* ]] || [[ "$out" == *Code:\ 497* ]]; then
    echo "PASS denied: $label"
  else
    echo "FAIL denied: $label"
    fail=1
  fi
}

expect_ok "audit_budget_runs all columns" \
  "SELECT run_id, run_completed_at, run_status, scope, scope_key, team, project, status, count FROM otel.audit_budget_runs LIMIT 1"
expect_ok "budget_status_current allowed columns" \
  "SELECT scope, scope_key, team, project, period_start, as_of_ts, is_stale, budget_status, action_on_breach FROM otel.budget_status_current LIMIT 1"
expect_ok "audit_cost_runs allowed columns" \
  "SELECT run_id, run_completed_at, run_status, events_dropped_late FROM otel.audit_cost_runs LIMIT 1"

expect_denied "budget_usd_micros" \
  "SELECT budget_usd_micros FROM otel.budget_status_current LIMIT 1"
expect_denied "fct_budget_consumption_daily" \
  "SELECT * FROM otel.fct_budget_consumption_daily LIMIT 1"
expect_denied "fct_cost_chargeback_daily" \
  "SELECT * FROM otel.fct_cost_chargeback_daily LIMIT 1"
expect_denied "seeds budgets" \
  "SELECT * FROM otel.budgets LIMIT 1"
expect_denied "otel_logs" \
  "SELECT * FROM otel.otel_logs LIMIT 1"
expect_denied "INSERT audit_budget_runs" \
  "INSERT INTO otel.audit_budget_runs SELECT * FROM otel.audit_budget_runs LIMIT 0"

grants="$(_admin "SHOW GRANTS FOR oteru_budget_view_definer")"
if [[ "$grants" == *fct_budget_consumption_daily* ]] && [[ "$grants" != *INSERT* ]]; then
  echo "PASS definer SHOW GRANTS (fct columns only, no INSERT)"
else
  echo "FAIL definer SHOW GRANTS"
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  echo "prove_budget_view_grants: FAILED" >&2
  exit 1
fi
echo "prove_budget_view_grants: OK"
