#!/usr/bin/env bash
# Every view in oteru-collector/clickhouse/views must CREATE (and re-apply) on a
# bare ClickHouse with DEFAULT settings, across the versions we claim to run.
# Review on #84 (silmarahp): on 26.6 `StatusCode = 'Error'` became
# LowCardinality(UInt8) and CREATE VIEW failed with
# SUSPICIOUS_TYPE_FOR_LOW_CARDINALITY — while `latest` accepted it, so the
# e2e (always `latest`) never saw it.
#
# Usage: bash scripts/check_views_compat.sh            # versions below
#        CLICKHOUSE_VERSIONS="26.6 latest" bash scripts/check_views_compat.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIEWS="$ROOT/oteru-collector/clickhouse/views"
TABLES="$ROOT/oteru-collector/clickhouse/test/otel_tables.sql"
VERSIONS="${CLICKHOUSE_VERSIONS:-26.6 latest}"
failures=0

echo "views compat (default settings) — ClickHouse: $VERSIONS"
for v in $VERSIONS; do
  name="oteru-views-compat-${v//./-}"
  docker rm -f "$name" >/dev/null 2>&1
  if ! docker run -d --rm --name "$name" -e CLICKHOUSE_USER=otel -e CLICKHOUSE_PASSWORD=otel \
      "clickhouse/clickhouse-server:$v" >/dev/null; then
    echo "  FAIL: clickhouse $v — could not start"; failures=$((failures + 1)); continue
  fi
  ch() { docker exec -i "$name" clickhouse-client --user otel --password otel "$@"; }
  for _ in $(seq 1 30); do ch -q "SELECT 1" >/dev/null 2>&1 && break; sleep 2; done
  real="$(ch -q 'SELECT version()' 2>/dev/null)"
  ch --multiquery < "$TABLES" >/dev/null || { echo "  FAIL: clickhouse $v — table stand-ins"; failures=$((failures + 1)); }
  for sql in "$VIEWS"/*.sql; do
    view="$(basename "$sql" .sql)"
    for pass in create reapply; do
      if ! out="$(ch --multiquery < "$sql" 2>&1)"; then
        echo "  FAIL: clickhouse $real — $view ($pass): $(grep -o 'Code: [0-9]*.*' <<<"$out" | head -1 | cut -c1-160)"
        failures=$((failures + 1)); continue 2
      fi
    done
    echo "  ok   clickhouse $real — $view creates and re-applies"
  done
  docker rm -f "$name" >/dev/null 2>&1
done

[ "$failures" -eq 0 ] && { echo; echo "views compat: OK"; exit 0; }
echo; echo "views compat: $failures failure(s)."; exit 1
