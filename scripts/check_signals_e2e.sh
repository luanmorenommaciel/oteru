#!/usr/bin/env bash
# Signal-selection e2e: proves the collector ingests any combination of signals
# without erroring, and that --emit sends exactly the selected ones.
#
# The collector needs no custom "normalization layer" for this — the OTLP
# receiver already dispatches each signal to its own pipeline, and a payload
# missing a signal simply never reaches that pipeline. This script is the
# verification artifact for that claim.
#
# Requires: `make up-clickhouse` running (collector + ClickHouse) and the venv
# from `make setup`. Run from the repo root: `make e2e-signals`.
#
# Isolation: every run tags its captures with an `oteru.e2e.run_id` resource
# attribute and filters every ClickHouse assertion by it, so live Claude Code
# telemetry or a concurrent e2e run on the same collector cannot change the
# expected row-count deltas. No environment quarantine is required.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EMITTER="$ROOT/oteru-emitter"
TINY="$EMITTER/tests/fixtures/tiny-capture.json"

# Trace captures are built, not committed — see oteru-emitter/tests/factories.py.
TRACES="$(mktemp -t oteru-traces-XXXXXX).json"
SCOPED_TINY="$(mktemp -t oteru-tiny-XXXXXX).json"
SCOPED_BENIGN="$(mktemp -t oteru-benign-XXXXXX).json"
SCOPED_TRACES="$(mktemp -t oteru-scoped-traces-XXXXXX).json"
MCP="$(mktemp -t oteru-mcp-XXXXXX).json"
SCOPED_MCP="$(mktemp -t oteru-scoped-mcp-XXXXXX).json"
MCP_EXPECTED="$(mktemp -t oteru-mcp-expected-XXXXXX).tsv"
API_REQUEST="$EMITTER/tests/fixtures/api_request_cost_fixture.json"
SCOPED_API_REQUEST="$(mktemp -t oteru-api-request-XXXXXX).json"
HMAC_DIR="$(mktemp -d -t oteru-hmac-XXXXXX)"
trap 'rm -f "$TRACES" "$SCOPED_TINY" "$SCOPED_BENIGN" "$SCOPED_TRACES" "$MCP" "$SCOPED_MCP" "$MCP_EXPECTED" "$SCOPED_API_REQUEST"; rm -rf "$HMAC_DIR"' EXIT
HMAC_IMAGE="${OTERU_COLLECTOR_IMAGE:-oteru-collector:0.155.0}"
if command -v podman >/dev/null 2>&1; then
  CTR=podman
elif command -v docker >/dev/null 2>&1; then
  CTR=docker
else
  CTR=""
fi
# Per-run HMAC keys (32+ bytes) and a 31-byte reject key. In-memory only:
# never written to a file, never echoed.
if command -v openssl >/dev/null 2>&1; then
  HMAC_KEY_A="$(openssl rand -hex 32)"
  HMAC_KEY_B="$(openssl rand -hex 32)"
  HMAC_SHORT="$(openssl rand -hex 16 | cut -c1-31)"
else
  HMAC_KEY_A="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  HMAC_KEY_B="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  HMAC_SHORT="$(python3 -c 'import secrets; print(secrets.token_hex(16)[:31])')"
fi
while [ "$HMAC_KEY_B" = "$HMAC_KEY_A" ]; do
  if command -v openssl >/dev/null 2>&1; then
    HMAC_KEY_B="$(openssl rand -hex 32)"
  else
    HMAC_KEY_B="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
done
# Unique per execution; tags every replayed batch so ClickHouse assertions can
# filter to rows this run produced. date+pid keeps it portable (Git Bash/macOS/Linux).
RUN_ID="e2e-$(date +%s)-$$"
CH="${CLICKHOUSE_URL:-http://localhost:8123/?user=otel&password=otel}"
OTLP="${OTLP_HTTP_ENDPOINT:-http://localhost:4318}"
SETTLE="${SETTLE_SECONDS:-6}"
if [ "${OS:-}" = "Windows_NT" ]; then
  PY="$EMITTER/.venv/Scripts/python.exe"
else
  PY="$EMITTER/.venv/bin/python"
fi

failures=0

fail() { echo "  FAIL: $*"; failures=$((failures + 1)); }

# Row counts filtered to this run's tag — concurrent telemetry (live or
# synthetic) on the same collector contributes nothing to these numbers.
counts() {
  curl -sf "$CH" --data-binary "SELECT
      (SELECT count() FROM otel.otel_logs WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'),
      (SELECT count() FROM otel.otel_traces WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'),
      (SELECT count() FROM otel.otel_metrics_sum WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID')
    FORMAT TSV"
}

# inject_run_id <src-capture> <dst-capture>: copy a JSONL capture with the
# oteru.e2e.run_id resource attribute added to every batch.
inject_run_id() {
  "$PY" - "$1" "$2" "$RUN_ID" <<'PY'
import json, sys

src, dst, run_id = sys.argv[1], sys.argv[2], sys.argv[3]
with open(src, encoding="utf-8") as fh:
    batches = [json.loads(line) for line in fh if line.strip()]
for batch in batches:
    for section in ("resourceLogs", "resourceMetrics", "resourceSpans"):
        for entry in batch.get(section, []):
            attrs = entry.setdefault("resource", {}).setdefault("attributes", [])
            attrs.append({"key": "oteru.e2e.run_id", "value": {"stringValue": run_id}})
with open(dst, "w", encoding="utf-8") as fh:
    for batch in batches:
        fh.write(json.dumps(batch) + "\n")
PY
}

# The mirror of case_emit: --emit naming a signal the capture lacks has to fail
# *and* send nothing. A partially-available selection (log,trace on a capture
# with no spans) is the case that used to slip through — it left batches behind,
# so the old "nothing survived the filter" guard never fired.
# case_emit_rejects <description> <capture> <emit>
case_emit_rejects() {
  local desc="$1" capture="$2" emit="$3"
  local before after
  before="$(counts)" || { fail "$desc: ClickHouse unreachable at $CH"; return; }
  IFS=$'\t' read -r l0 t0 m0 <<<"$before"

  if "$PY" -m oteru_emitter.cli replay "$capture" \
      --transport http --endpoint "$OTLP" --max-gap 0.2 --emit "$emit" >/dev/null 2>&1; then
    fail "$desc: --emit $emit should have exited non-zero"
    return
  fi
  sleep "$SETTLE"

  after="$(counts)"
  IFS=$'\t' read -r l1 t1 m1 <<<"$after"
  local got="$((l1 - l0))/$((t1 - t0))/$((m1 - m0))"
  if [ "$got" = "0/0/0" ]; then
    echo "  ok   --emit $emit -> rejected, nothing sent"
  else
    fail "--emit $emit was rejected but still sent +$got"
  fi
}

# case <description> <capture> <emit> <expect-logs> <expect-traces> <expect-metrics> [transport]
# transport defaults to http; pass grpc to exercise the :4317 receiver instead.
case_emit() {
  local desc="$1" capture="$2" emit="$3" exp_l="$4" exp_t="$5" exp_m="$6"
  local transport="${7:-http}"
  local before after
  before="$(counts)" || { fail "$desc: ClickHouse unreachable at $CH"; return; }
  IFS=$'\t' read -r l0 t0 m0 <<<"$before"

  if ! "$PY" -m oteru_emitter.cli replay "$capture" \
      --transport http --endpoint "$OTLP" --max-gap 0.2 --emit "$emit" >/dev/null 2>&1; then
    fail "$desc: emitter exited non-zero"
    return
  fi
  sleep "$SETTLE"

  after="$(counts)"
  IFS=$'\t' read -r l1 t1 m1 <<<"$after"
  local got="$((l1 - l0))/$((t1 - t0))/$((m1 - m0))"
  local want="$exp_l/$exp_t/$exp_m"
  if [ "$got" = "$want" ]; then
    echo "  ok   [$transport] --emit $emit -> logs/traces/metrics +$got"
  else
    fail "[$transport] --emit $emit expected +$want, got +$got"
  fi
}

echo "signal-selection e2e ($OTLP + gRPC :4317, ClickHouse)"
cd "$EMITTER" || exit 1

"$PY" - "$TRACES" <<'PY' || { echo "  FAIL: could not build the traces capture"; exit 1; }
import json, sys, pathlib
sys.path.insert(0, "tests")
from factories import traces_capture

pathlib.Path(sys.argv[1]).write_text(
    "".join(json.dumps(b) + "\n" for b in traces_capture()), encoding="utf-8"
)
PY

inject_run_id "$TINY" "$SCOPED_TINY" \
  || { echo "  FAIL: could not tag the tiny capture"; exit 1; }

# Privacy rejects metric series with principal identity labels. Keep the
# signal-selection contract focused on benign metric delivery; the privacy E2E
# separately proves that identified series are rejected.
"$PY" - "$SCOPED_TINY" "$SCOPED_BENIGN" <<'PY' \
  || { echo "  FAIL: could not build benign metric capture"; exit 1; }
import json, sys

identity = {"user.email", "user.id", "user.account_uuid", "user.account_id", "organization.id", "host.name"}
with open(sys.argv[1], encoding="utf-8") as src, open(sys.argv[2], "w", encoding="utf-8") as dst:
    for line in src:
        batch = json.loads(line)
        for resource in batch.get("resourceMetrics", []):
            resource["resource"]["attributes"] = [
                attr for attr in resource["resource"].get("attributes", [])
                if attr["key"] not in identity
            ]
            for scope in resource.get("scopeMetrics", []):
                scope["scope"]["attributes"] = [
                    attr for attr in scope.get("scope", {}).get("attributes", [])
                    if attr["key"] not in identity
                ]
                for metric in scope.get("metrics", []):
                    for family in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
                        for point in metric.get(family, {}).get("dataPoints", []):
                            point["attributes"] = [
                                attr for attr in point.get("attributes", [])
                                if attr["key"] not in identity
                            ]
        dst.write(json.dumps(batch) + "\n")
PY
inject_run_id "$TRACES" "$SCOPED_TRACES" \
  || { echo "  FAIL: could not tag the traces capture"; exit 1; }

case_emit "logs only"    "$SCOPED_TINY"   "log"        3 0 0
case_emit "metrics only" "$SCOPED_BENIGN" "metric"     0 0 1
case_emit "traces only"  "$SCOPED_TRACES" "trace"      0 6 0
case_emit "combined"     "$SCOPED_BENIGN" "log,metric" 3 0 1
case_emit_rejects "partially absent" "$SCOPED_TINY" "log,trace"

# Same signals over gRPC (:4317). Both receivers feed the same per-signal
# pipelines, so the deltas must match the HTTP cases exactly (#40).
case_emit "logs only (grpc)"    "$SCOPED_TINY"   "log"        3 0 0 grpc
case_emit "metrics only (grpc)" "$SCOPED_TINY"   "metric"     0 0 1 grpc
case_emit "traces only (grpc)"  "$SCOPED_TRACES" "trace"      0 6 0 grpc
case_emit "combined (grpc)"     "$SCOPED_TINY"   "log,metric" 3 0 1 grpc

# Replaying the same capture twice must yield two distinct traces. Trace/span
# IDs are structural OTLP fields, so nothing rotated them before and every
# replay collided with the original trace. Run-scoped query, so neither
# pre-existing rows nor concurrent telemetry can confound it.
traces_before="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces \
   WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID' FORMAT TSV")"
for _ in 1 2; do
  if ! "$PY" -m oteru_emitter.cli replay "$SCOPED_TRACES" \
    --transport http --endpoint "$OTLP" --max-gap 0.2 --emit trace >/dev/null 2>&1; then
    fail "trace replay exited non-zero"
  fi
done
sleep "$SETTLE"
traces_after="$(curl -sf "$CH" --data-binary \
  "SELECT uniqExact(TraceId) FROM otel.otel_traces \
   WHERE ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID' FORMAT TSV")"
new_traces=$((traces_after - traces_before))
if [ "$new_traces" -eq 4 ]; then
  echo "  ok   two replays -> 4 distinct traces (no ID collision)"
else
  fail "two replays of a 2-trace capture yielded $new_traces distinct traces (expected 4)"
fi

# MCP calls as first-class rows (#42): replay the MCP fixture and read it back
# through the otel.mcp_calls view — per-method counts must equal what the
# fixture declares, the failed tool call must surface as failed, and the
# client/server sides of one call must join on (trace_id, request_id).
"$PY" - "$MCP" "$MCP_EXPECTED" <<'PY' || fail "could not build the MCP capture"
import json, sys, pathlib
sys.path.insert(0, "tests")
from factories import MCP_EXPECTED_METHODS, mcp_capture

pathlib.Path(sys.argv[1]).write_text(
    "".join(json.dumps(b) + "\n" for b in mcp_capture()), encoding="utf-8"
)
pathlib.Path(sys.argv[2]).write_text(
    "".join(f"{m}\t{n}\n" for m, n in sorted(MCP_EXPECTED_METHODS.items())), encoding="utf-8"
)
PY
inject_run_id "$MCP" "$SCOPED_MCP" || fail "could not tag the MCP capture"
# ClickHouse 24.8 rejects LowCardinality(UInt8) from `StatusCode = 'Error'`
# unless this setting is on; Compose images used by make up-clickhouse accept it.
MCP_CH="$CH"
case "$MCP_CH" in
  *\?*) MCP_CH="${MCP_CH}&allow_suspicious_low_cardinality_types=1" ;;
  *) MCP_CH="${MCP_CH}?allow_suspicious_low_cardinality_types=1" ;;
esac
if ! curl -sf "$MCP_CH" --data-binary @"$ROOT/oteru-collector/clickhouse/views/mcp_calls.sql" >/dev/null; then
  fail "could not apply the otel.mcp_calls view"
elif ! "$PY" -m oteru_emitter.cli replay "$SCOPED_MCP" --profile generic \
    --transport http --max-gap 0.2 >/dev/null 2>&1; then
  fail "MCP replay exited non-zero"
else
  sleep "$SETTLE"
  scope="resource_attributes['oteru.e2e.run_id'] = '$RUN_ID'"
  got="$(curl -sf "$CH" --data-binary \
    "SELECT method, count() FROM otel.mcp_calls WHERE $scope GROUP BY method ORDER BY method FORMAT TSV")"
  if [ "$got" = "$(cat "$MCP_EXPECTED")" ]; then
    echo "  ok   otel.mcp_calls -> per-method counts match the fixture"
  else
    fail "otel.mcp_calls per-method counts: got [$got], expected [$(cat "$MCP_EXPECTED")]"
  fi
  failed="$(curl -sf "$CH" --data-binary \
    "SELECT tool_name, error_type FROM otel.mcp_calls WHERE $scope AND failed FORMAT TSV")"
  if [ "$failed" = $'refund_order\ttool_error' ]; then
    echo "  ok   otel.mcp_calls -> failed tool call surfaces with its error.type"
  else
    fail "otel.mcp_calls failed rows: got [$failed]"
  fi
  joined="$(curl -sf "$CH" --data-binary \
    "SELECT count() FROM otel.mcp_calls c JOIN otel.mcp_calls s
       ON c.trace_id = s.trace_id AND c.request_id = s.request_id AND s.parent_span_id = c.span_id
     WHERE c.side = 'client' AND s.side = 'server'
       AND c.$scope AND s.$scope FORMAT TSV")"
  if [ "$joined" = "1" ]; then
    echo "  ok   otel.mcp_calls -> client and server sides of a call join"
  else
    fail "otel.mcp_calls client/server join: got $joined row(s), expected 1"
  fi
fi

# API request staging view (#45, #47): replay the api_request fixture and
# assert via otel.stg_api_request that synthetic traffic is excluded, dedup
# keys are correct, cost totals match, and no PII leaks.
inject_run_id "$API_REQUEST" "$SCOPED_API_REQUEST" || fail "could not tag the API request fixture"
if ! curl -sf "$CH" --data-binary @"$ROOT/oteru-collector/clickhouse/views/stg_api_request.sql" >/dev/null; then
  fail "could not apply the otel.stg_api_request view"
elif ! "$PY" -m oteru_emitter.cli replay "$SCOPED_API_REQUEST" \
    --transport http --max-gap 0.2 >/dev/null 2>&1; then
  fail "API request replay exited non-zero"
else
  sleep "$SETTLE"
  scope="ResourceAttributes['oteru.e2e.run_id'] = '$RUN_ID'"
  
  # 9 rows visible (1 synthetic excluded from 10-row fixture)
  # Filter to only our fixture's events (which have non-NULL cost)
  row_count="$(curl -sf "$CH" --data-binary \
    "SELECT count() FROM otel.stg_api_request WHERE $scope AND cost_usd_micros IS NOT NULL FORMAT TSV")"
  if [ "$row_count" = "9" ]; then
    echo "  ok   otel.stg_api_request -> 9 rows (1 synthetic excluded)"
  else
    fail "otel.stg_api_request row count: got $row_count, expected 9"
  fi
  
  # 8 distinct dedup keys (2 duplicates by request_id -> 8 unique)
  # Filter to only our fixture's events (which have non-NULL cost)
  dedup_count="$(curl -sf "$CH" --data-binary \
    "SELECT uniqExact(dedup_key) FROM otel.stg_api_request WHERE $scope AND cost_usd_micros IS NOT NULL FORMAT TSV")"
  if [ "$dedup_count" = "8" ]; then
    echo "  ok   otel.stg_api_request -> 8 distinct dedup keys"
  else
    fail "otel.stg_api_request dedup keys: got $dedup_count, expected 8"
  fi
  
  # Exact sum of cost_usd_micros (sum of non-synthetic events)
  # Expected: 123450 + 123450 + 2000 + 250000 + 75000 + 1000 + 150000 + 80000 + 1500 = 806400
  # Automatically filters NULLs since sum() ignores them
  cost_sum="$(curl -sf "$CH" --data-binary \
    "SELECT sum(cost_usd_micros) FROM otel.stg_api_request WHERE $scope AND cost_usd_micros IS NOT NULL FORMAT TSV")"
  if [ "$cost_sum" = "806400" ]; then
    echo "  ok   otel.stg_api_request -> cost sum matches expected (806400 micros)"
  else
    fail "otel.stg_api_request cost sum: got $cost_sum, expected 806400"
  fi
  
  # No column contains '@' (every field of the staging row, as JSON).
  pii_check="$(curl -sf "$CH" --data-binary \
    "SELECT count() FROM otel.stg_api_request WHERE $scope AND cost_usd_micros IS NOT NULL
     AND positionCaseInsensitive(toJSONString(tuple(*)), '@') > 0 FORMAT TSV")"
  if [ "$pii_check" = "0" ]; then
    echo "  ok   otel.stg_api_request -> no @ found in any column (PII clean)"
  else
    fail "otel.stg_api_request PII check: found $pii_check rows with @"
  fi

  hmac_bad="$(curl -sf "$CH" --data-binary \
    "SELECT count() FROM otel.stg_api_request WHERE $scope AND cost_usd_micros IS NOT NULL
     AND NOT match(actor_id, '^hmac:v1:[0-9a-f]{64}\$') FORMAT TSV")"
  if [ "$hmac_bad" = "0" ]; then
    echo "  ok   otel.stg_api_request -> actor_id matches ^hmac:v1:[0-9a-f]{64}$"
  else
    fail "otel.stg_api_request actor_id format: $hmac_bad row(s) not hmac:v1:<64 hex>"
  fi

  # Same HMAC key + same email => one actor_id; the fixture has two emails.
  hmac_n="$(curl -sf "$CH" --data-binary \
    "SELECT uniqExact(actor_id) FROM otel.stg_api_request
     WHERE $scope AND cost_usd_micros IS NOT NULL FORMAT TSV")"
  if [ "$hmac_n" = "2" ]; then
    echo "  ok   otel.stg_api_request -> same key yields two stable actor_ids (two emails)"
  else
    fail "otel.stg_api_request same-key stability: uniqExact(actor_id)=$hmac_n (expected 2)"
  fi
fi

# HMAC fail-closed + different-key identity against the local OCB image.
# An unavailable podman/docker or missing image is a failure, not a skip.
hmac_extract_email() {
  "$PY" - "$1" <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.exists():
    sys.exit(0)
text = path.read_text(encoding="utf-8")
found = []
for line in text.splitlines():
    if not line.strip():
        continue
    batch = json.loads(line)
    for resource in batch.get("resourceLogs", []):
        for scope in resource.get("scopeLogs", []):
            for rec in scope.get("logRecords", []):
                for attr in rec.get("attributes", []):
                    if attr.get("key") == "user.email":
                        found.append(attr["value"]["stringValue"])
print("\n".join(found))
PY
}

hmac_fail_closed() {
  local label="$1"
  shift
  local log status=0
  log="$(timeout 25 "$CTR" run --rm --network none \
    -v "$HMAC_DIR/config.yml:/etc/oteru-collector-config.yml:ro" \
    -v "$HMAC_DIR/privacy:/etc/oteru-privacy:ro" \
    -v "$HMAC_DIR/empty:/telemetry" \
    "$@" \
    "$HMAC_IMAGE" --config=/etc/oteru-collector-config.yml 2>&1)" || status=$?
  if [ "$status" -eq 0 ] || [ "$status" -eq 124 ]; then
    fail "HMAC $label: collector started (exit $status); expected non-zero fail-closed"
    printf '%s\n' "$log"
    return
  fi
  if ! printf '%s\n' "$log" | grep -Eqi 'unset|empty|shorter than 32'; then
    fail "HMAC $label: collector exited $status but did not log the key failure"
    printf '%s\n' "$log"
    return
  fi
  local leaked=0
  for needle in "$HMAC_KEY_A" "$HMAC_KEY_B" "$HMAC_SHORT"; do
    if printf '%s\n' "$log" | grep -F -- "$needle" >/dev/null; then
      leaked=1
    fi
  done
  if [ "$leaked" -eq 1 ]; then
    fail "HMAC $label: key string appeared in collector logs"
    return
  fi
  echo "  ok   HMAC $label -> exit $status, failure logged, key absent from logs"
}

hmac_isolated_checks() {
  echo "HMAC isolated checks ($HMAC_IMAGE via ${CTR:-none})"
  if [ -z "$CTR" ]; then
    fail "HMAC isolated checks: podman/docker unavailable (need image $HMAC_IMAGE)"
    return
  fi
  if ! "$CTR" image inspect "$HMAC_IMAGE" >/dev/null 2>&1; then
    fail "HMAC isolated checks: image $HMAC_IMAGE is not present"
    return
  fi

  mkdir -p "$HMAC_DIR/privacy" "$HMAC_DIR/empty" "$HMAC_DIR/out-a" "$HMAC_DIR/out-b"
  chmod 0777 "$HMAC_DIR/empty" "$HMAC_DIR/out-a" "$HMAC_DIR/out-b"
  cp "$ROOT/oteru-collector/oteru-collector-config.yml" "$HMAC_DIR/config.yml"
  if ! "$PY" "$ROOT/oteru-collector/privacy/compile.py" \
      --policy "$ROOT/oteru-collector/privacy/policy.json" \
      --output "$HMAC_DIR/privacy/processors.json"; then
    fail "HMAC isolated checks: privacy compile.py failed"
    return
  fi

  hmac_fail_closed "key unset" 
  hmac_fail_closed "key empty" -e OTERU_PII_HMAC_KEY=
  hmac_fail_closed "key 31 bytes" -e "OTERU_PII_HMAC_KEY=$HMAC_SHORT"

  hmac_run_keyed() {
    local key="$1" outdir="$2" name="$3"
    local cid http i code
    cid="$("$CTR" run -d --name "$name" \
      -e "OTERU_PII_HMAC_KEY=$key" \
      -p 127.0.0.1::4318 \
      -v "$HMAC_DIR/config.yml:/etc/oteru-collector-config.yml:ro" \
      -v "$HMAC_DIR/privacy:/etc/oteru-privacy:ro" \
      -v "$outdir:/telemetry" \
      "$HMAC_IMAGE" --config=/etc/oteru-collector-config.yml)" || {
      fail "HMAC keyed run: $CTR run $name failed"
      return 1
    }
    http="http://127.0.0.1:$("$CTR" port "$name" 4318/tcp | head -1 | awk -F: '{print $NF}')"
    code=""
    for i in $(seq 1 50); do
      code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$http/v1/logs" \
        -H 'content-type: application/json' --data '{}' || true)"
      if [ "$code" = "200" ]; then
        break
      fi
      sleep 0.2
    done
    if [ "$code" != "200" ]; then
      fail "HMAC keyed run $name: collector did not become ready"
      "$CTR" logs "$name" || true
      "$CTR" rm -f "$name" >/dev/null 2>&1 || true
      return 1
    fi
    local payload='{"resourceLogs":[{"resource":{"attributes":[]},"scopeLogs":[{"scope":{},"logRecords":[{"body":{"stringValue":"hmac-e2e"},"attributes":[{"key":"user.email","value":{"stringValue":"user@example.com"}}]}]}]}]}'
    curl -sf -X POST "$http/v1/logs" -H 'content-type: application/json' --data "$payload" >/dev/null
    curl -sf -X POST "$http/v1/logs" -H 'content-type: application/json' --data "$payload" >/dev/null
    sleep 2
    local logs
    logs="$("$CTR" logs "$name" 2>&1 || true)"
    # Rootless podman writes bind-mount files as a mapped uid the host cannot
    # read; copy out as the invoking user before removing the container.
    "$CTR" cp "$name:/telemetry/telemetry.json" "$outdir/telemetry.json" 2>/dev/null || true
    if [ "$CTR" = podman ]; then
      podman unshare chmod -R a+rX "$outdir" >/dev/null 2>&1 || true
    fi
    "$CTR" rm -f "$name" >/dev/null 2>&1 || true
    if printf '%s\n' "$logs" | grep -F -- "$key" >/dev/null; then
      fail "HMAC keyed run $name: key string appeared in collector logs"
      return 1
    fi
    return 0
  }

  hmac_run_keyed "$HMAC_KEY_A" "$HMAC_DIR/out-a" "oteru-hmac-a-$$" || true
  hmac_run_keyed "$HMAC_KEY_B" "$HMAC_DIR/out-b" "oteru-hmac-b-$$" || true

  local ids_a ids_b first_a second_a first_b
  ids_a="$(hmac_extract_email "$HMAC_DIR/out-a/telemetry.json")"
  ids_b="$(hmac_extract_email "$HMAC_DIR/out-b/telemetry.json")"
  first_a="$(printf '%s\n' "$ids_a" | awk 'NF{print; exit}')"
  second_a="$(printf '%s\n' "$ids_a" | awk 'NF{n++; if(n==2){print; exit}}')"
  first_b="$(printf '%s\n' "$ids_b" | awk 'NF{print; exit}')"

  if [[ "$first_a" =~ ^hmac:v1:[0-9a-f]{64}$ ]]; then
    echo "  ok   HMAC key A actor_id matches ^hmac:v1:[0-9a-f]{64}$"
  else
    fail "HMAC key A actor_id format: [$first_a]"
  fi
  if [ -n "$first_a" ] && [ "$first_a" = "$second_a" ]; then
    echo "  ok   HMAC same key -> same actor_id"
  else
    fail "HMAC same key -> same id: got [$first_a] then [$second_a]"
  fi
  if [[ "$first_b" =~ ^hmac:v1:[0-9a-f]{64}$ ]] && [ -n "$first_a" ] && [ "$first_a" != "$first_b" ]; then
    echo "  ok   HMAC different key -> different actor_id"
  else
    fail "HMAC different key -> different id: A=[$first_a] B=[$first_b]"
  fi
}

hmac_isolated_checks

# A payload with no records at all must be accepted, not rejected.
for path in v1/logs v1/metrics v1/traces; do
  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$OTLP/$path" \
    -H 'content-type: application/json' --data '{}')"
  if [ "$code" = "200" ]; then
    echo "  ok   empty payload POST /$path -> 200"
  else
    fail "empty payload POST /$path -> $code (expected 200)"
  fi
done

if [ "$failures" -ne 0 ]; then
  echo
  echo "e2e-signals: $failures check(s) failed."
  exit 1
fi
echo
echo "e2e-signals: OK — every signal combination ingested, no collector error."
