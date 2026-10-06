#!/usr/bin/env bash
# Dual-retention e2e (#44): proves one replay lands in BOTH stores and that the
# cold copy is actually immutable.
#
#   hot  — the rows reach ClickHouse, whose tables carry a TTL;
#   cold — every batch is archived as an object in the S3 bucket, readable back
#          from ClickHouse with s3() (the auditor's path), and deleting an
#          archived object version is refused by Object Lock.
#
# Requires: `make up-archive` running and the venv from `make setup`. Run from
# the repo root: `make e2e-archive`. Same isolation as check_signals_e2e.sh:
# everything is filtered by a per-run `oteru.e2e.run_id` resource attribute.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMITTER="$ROOT/oteru-emitter"
TINY="$EMITTER/tests/fixtures/tiny-capture.json"  # 2 logs batches + 1 metrics batch
SCOPED="$(mktemp -t oteru-archive-XXXXXX).json"
trap 'rm -f "$SCOPED"' EXIT
RUN_ID="e2e-archive-$(date +%s)-$$"
CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
SETTLE="${SETTLE_SECONDS:-8}"
ACCESS_KEY="${OTERU_ARCHIVE_ACCESS_KEY:-oteru-archive}"
SECRET_KEY="${OTERU_ARCHIVE_SECRET_KEY:-oteru-archive-dev-secret}"
BUCKET="${OTERU_ARCHIVE_BUCKET:-oteru-archive}"
# ClickHouse and the archive share the compose network, so ClickHouse reads the
# bucket by service name; the AWS CLI container joins the same network.
S3_URL="http://archive:9000/$BUCKET/**/*.json.gz"
NETWORK="${COMPOSE_NETWORK:-oteru-collector_default}"
if [ "${OS:-}" = "Windows_NT" ]; then
  PY="$EMITTER/.venv/Scripts/python.exe"
else
  PY="$EMITTER/.venv/bin/python"
fi

failures=0
fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }
ok() { echo "  ok   $*"; }
q() { curl -sf "$CH" --data-binary "$1"; }
aws_s3api() {
  docker run --rm --network "$NETWORK" \
    -e AWS_ACCESS_KEY_ID="$ACCESS_KEY" -e AWS_SECRET_ACCESS_KEY="$SECRET_KEY" \
    -e AWS_DEFAULT_REGION=us-east-1 \
    amazon/aws-cli --endpoint-url http://archive:9000 s3api "$@"
}
cold_objects() {
  q "SELECT _path FROM s3('$S3_URL', '$ACCESS_KEY', '$SECRET_KEY', 'JSONAsString', 'json String', 'gzip')
     WHERE position(json, '$RUN_ID') > 0 ORDER BY _path FORMAT TSV"
}

echo "dual-retention e2e (ClickHouse hot + S3 archive cold)"

"$PY" - "$TINY" "$SCOPED" "$RUN_ID" <<'PY' || { echo "  FAIL: could not tag the capture"; exit 1; }
import json, sys

src, dst, run_id = sys.argv[1:4]
with open(src, encoding="utf-8") as fh:
    batches = [json.loads(line) for line in fh if line.strip()]
for batch in batches:
    for section in ("resourceLogs", "resourceMetrics", "resourceSpans"):
        for entry in batch.get(section, []):
            attrs = entry.setdefault("resource", {}).setdefault("attributes", [])
            attrs.append({"key": "oteru.e2e.run_id", "value": {"stringValue": run_id}})
with open(dst, "w", encoding="utf-8") as fh:
    fh.writelines(json.dumps(b) + "\n" for b in batches)
PY

if ! "$PY" -m oteru_emitter.cli replay "$SCOPED" --transport http --max-gap 0.2 >/dev/null 2>&1; then
  echo "  FAIL: replay exited non-zero (is \`make up-archive\` running?)"
  exit 1
fi
sleep "$SETTLE"

# --- hot ------------------------------------------------------------------------
hot="$(q "SELECT count() FROM otel.otel_logs
          WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID' FORMAT TSV")"
if [ "$hot" = "3" ]; then ok "hot: 3 log records in otel.otel_logs"; else fail "hot: got $hot log records, expected 3"; fi

ttl="$(q "SELECT countIf(engine_full LIKE '% TTL %') FROM system.tables
          WHERE database = 'otel' AND name IN ('otel_logs', 'otel_traces', 'otel_metrics_sum') FORMAT TSV")"
if [ "$ttl" = "3" ]; then ok "hot: logs/traces/metrics tables carry a TTL"; else fail "hot: $ttl of 3 tables carry a TTL"; fi

# --- cold -----------------------------------------------------------------------
objects="$(cold_objects)" || objects=""
n_logs="$(grep -c '/logs_' <<<"$objects" || true)"
n_metrics="$(grep -c '/metrics_' <<<"$objects" || true)"
if [ "$n_logs/$n_metrics" = "2/1" ]; then
  ok "cold: 3 archived objects (2 logs, 1 metrics), read back through ClickHouse s3()"
else
  fail "cold: archived objects logs/metrics = $n_logs/$n_metrics, expected 2/1"
fi

# Immutability: deleting a specific archived *version* must be refused. (A plain
# DELETE only adds a delete marker on a versioned bucket — the version and its
# lock survive — so the version delete is the real test.)
key="$(head -n1 <<<"$objects" | sed "s#^$BUCKET/##")"
if [ -z "$key" ]; then
  fail "cold: no archived object to test the lock on"
else
  version="$(aws_s3api list-object-versions --bucket "$BUCKET" --prefix "$key" \
    --query 'Versions[0].VersionId' --output text 2>/dev/null)"
  if out="$(aws_s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$version" 2>&1)"; then
    fail "cold: deleting a locked version succeeded — Object Lock is not enforced"
  elif grep -qi "WORM\|object lock\|AccessDenied" <<<"$out"; then
    ok "cold: deleting an archived version is refused (Object Lock)"
  else
    fail "cold: delete failed for an unexpected reason: $out"
  fi
  still="$(aws_s3api list-object-versions --bucket "$BUCKET" --prefix "$key" \
    --query 'length(Versions)' --output text 2>/dev/null)"
  if [ "$still" = "1" ]; then ok "cold: the archived version is still there"; else fail "cold: versions after delete attempt: $still"; fi
fi

if [ "$failures" -ne 0 ]; then
  echo
  echo "e2e-archive: $failures check(s) failed."
  exit 1
fi
echo
echo "e2e-archive: OK — hot and cold both hold the run; the cold copy is immutable."
