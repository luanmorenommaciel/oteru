#!/usr/bin/env bash
# Apply scripts/bootstrap_budget_roles.sql to ClickHouse.
# Never prints, logs, or writes a password or HMAC key. `set -x` is not used.
#   CI / local: committed SQL (no-password HOST LOCAL).
#   Production: OTERU_BUDGET_DEFINER_PASSWORD is substituted in-memory only.
# Env: CLICKHOUSE_URL (default http://127.0.0.1:8123/), CLICKHOUSE_USER,
#      CLICKHOUSE_PASSWORD (admin DDL; never printed).
# Flags: --require  fail if ClickHouse is unreachable (evidence / local dbt).
#        --syntax   validate scripts + `docker compose config --quiet` only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQL_FILE="$ROOT/scripts/bootstrap_budget_roles.sql"
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

if [[ "$SYNTAX" -eq 1 ]]; then
  bash -n "$0"
  if command -v docker >/dev/null 2>&1 \
    && [[ -f "$ROOT/oteru-collector/docker-compose.yml" ]]; then
    (
      cd "$ROOT/oteru-collector"
      docker compose -f docker-compose.yml config --quiet
    )
  fi
  echo "bootstrap_budget_roles: syntax OK"
  exit 0
fi

CH="${CLICKHOUSE_URL:-http://127.0.0.1:8123/}"
ADMIN_USER="${CLICKHOUSE_USER:-default}"

_ch() {
  if [[ -n "${CLICKHOUSE_PASSWORD:-}" ]]; then
    curl -sS -f "$CH" \
      -H "X-ClickHouse-User: ${ADMIN_USER}" \
      -H "X-ClickHouse-Key: ${CLICKHOUSE_PASSWORD}" \
      --data-binary @-
  else
    curl -sS -f "$CH" \
      -H "X-ClickHouse-User: ${ADMIN_USER}" \
      --data-binary @-
  fi
}

if ! printf 'SELECT 1\n' | _ch >/dev/null 2>&1; then
  if [[ "$REQUIRE" -eq 1 ]]; then
    echo "bootstrap_budget_roles: ClickHouse unreachable" >&2
    exit 1
  fi
  echo "bootstrap_budget_roles: ClickHouse unreachable; skipped"
  exit 0
fi

# Substitute IDENTIFIED clause in-memory. stdout is the SQL pipe; stderr only.
python3 - "$SQL_FILE" <<'PY' | _ch >/dev/null
import os
import sys
from pathlib import Path

sql = Path(sys.argv[1]).read_text(encoding="utf-8")
needle = "IDENTIFIED WITH no_password HOST LOCAL"
pw = os.environ.get("OTERU_BUDGET_DEFINER_PASSWORD", "")
if pw:
    escaped = pw.replace("\\", "\\\\").replace("'", "\\'")
    sql = sql.replace(needle, f"IDENTIFIED WITH sha256_password BY '{escaped}' HOST LOCAL")
sys.stdout.write(sql)
PY

echo "bootstrap_budget_roles: OK"
