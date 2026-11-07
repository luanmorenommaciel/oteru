#!/usr/bin/env bash
# Regression for the Windows checkout (review on #88/#89, VitorDCM): with
# core.autocrlf, schema.sql arrives with \r\n line endings, the ingestors split
# the DDL on "\n;\n", ClickHouse receives several statements in one request,
# answers 400, and /healthz stays 503 forever.
#
# For each implementation: build its image, start it with a CRLF copy of
# schema.sql mounted over the bundled one, and require /healthz = 200.
# .gitattributes now pins *.sql to LF, so this guards the parser, not the
# checkout. Needs ClickHouse on the collector's compose network
# (`make up-clickhouse` or `make up-ingestor`).
#
# Usage: bash scripts/check_ingestor_crlf.sh [python go rust]

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK="${COMPOSE_NETWORK:-oteru-collector_default}"
if [ $# -gt 0 ]; then IMPLS=("$@"); else IMPLS=(python go rust); fi
WORK="$(mktemp -d -t oteru-crlf-XXXXXX)"
trap 'rm -rf "$WORK"; for i in "${IMPLS[@]}"; do docker rm -f "oteru-crlf-$i" >/dev/null 2>&1; done' EXIT

sed 's/$/\r/' "$ROOT/oteru-ingestor/schema.sql" > "$WORK/schema.sql"
grep -q $'\r' "$WORK/schema.sql" || { echo "FAIL: could not build a CRLF schema"; exit 1; }

failures=0
echo "ingestor CRLF schema check ($NETWORK)"
for impl in "${IMPLS[@]}"; do
  image="oteru-ingestor-$impl:crlf-check"
  if ! docker build -q -t "$image" -f "$ROOT/oteru-ingestor/$impl/Dockerfile" "$ROOT/oteru-ingestor" >/dev/null; then
    echo "  FAIL: $impl — image build failed"; failures=$((failures + 1)); continue
  fi
  docker rm -f "oteru-crlf-$impl" >/dev/null 2>&1
  docker run -d --name "oteru-crlf-$impl" --network "$NETWORK" \
    -e CLICKHOUSE_URL=http://clickhouse:8123 -e CLICKHOUSE_USER="${CLICKHOUSE_USER:-otel}" \
    -e CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-otel}" -e INGEST_DB="crlf_$impl" \
    -v "$WORK/schema.sql:/app/schema.sql:ro" "$image" >/dev/null
  code=000
  for _ in $(seq 1 20); do
    code="$(docker run --rm --network "$NETWORK" curlimages/curl:latest -s -o /dev/null \
      -w '%{http_code}' "http://oteru-crlf-$impl:4318/healthz" 2>/dev/null)"
    [ "$code" = "200" ] && break
    sleep 1
  done
  if [ "$code" = "200" ]; then
    echo "  ok   $impl — CRLF schema applied, /healthz 200"
  else
    echo "  FAIL: $impl — /healthz $code with a CRLF schema.sql"
    docker logs "oteru-crlf-$impl" 2>&1 | tail -2 | sed 's/^/         /'
    failures=$((failures + 1))
  fi
done

[ "$failures" -eq 0 ] && { echo; echo "ingestor CRLF check: OK"; exit 0; }
echo; echo "ingestor CRLF check: $failures implementation(s) failed."; exit 1
