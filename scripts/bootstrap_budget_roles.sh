#!/usr/bin/env bash
# Apply scripts/bootstrap_budget_roles.sql to ClickHouse.
# Never prints, logs, or writes a password or HMAC key. `set -x` is not used.
#   CI / local: committed SQL (no-password HOST LOCAL).
#   Production: OTERU_BUDGET_DEFINER_PASSWORD is substituted in-memory only.
# Env: CLICKHOUSE_URL (default http://127.0.0.1:8123/), CLICKHOUSE_USER,
#      CLICKHOUSE_PASSWORD (admin DDL; never printed).
# Flags: --require  fail if ClickHouse is unreachable (evidence / local dbt).
#        --syntax   validate scripts + split (each piece is one statement) +
#                   `docker compose config --quiet` only.
# HTTP only: one statement per request (ClickHouse Code 62 otherwise).
# No native client (must work on Windows / Git Bash).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILE="$ROOT/scripts/bootstrap_budget_roles.sql"
HELPER="$ROOT/scripts/bootstrap_budget_sql.py"
REQUIRE=0
SYNTAX=0
for arg in "$@"; do
  case "$arg" in
    --require) REQUIRE=1 ;;
    --syntax) SYNTAX=1 ;;
    -h|--help)
      echo "usage: bootstrap_budget_roles.sh [--require|--syntax]"
      exit 0
      ;;
    *)
      echo "bootstrap_budget_roles: unknown flag" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$SQL_FILE" ]]; then
  echo "bootstrap_budget_roles: missing SQL file" >&2
  exit 2
fi
if [[ ! -f "$HELPER" ]]; then
  echo "bootstrap_budget_roles: missing SQL helper" >&2
  exit 2
fi

if [[ "$SYNTAX" -eq 1 ]]; then
  bash -n "$0"
  python3 "$HELPER" --syntax "$SQL_FILE"
  if command -v docker >/dev/null 2>&1 \
    && [[ -f "$ROOT/oteru-collector/docker-compose.yml" ]]; then
    (
      cd "$ROOT/oteru-collector"
      docker compose -f docker-compose.yml config --quiet
    )
  fi
  exit 0
fi

if python3 "$HELPER" --probe; then
  python3 "$HELPER" --apply "$SQL_FILE"
  echo "bootstrap_budget_roles: OK"
  exit 0
fi

if [[ "$REQUIRE" -eq 1 ]]; then
  echo "bootstrap_budget_roles: ClickHouse unreachable" >&2
  exit 1
fi
echo "bootstrap_budget_roles: ClickHouse unreachable; skipped"
exit 0
