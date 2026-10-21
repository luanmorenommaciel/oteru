#!/usr/bin/env bash
# #46 fase 1: gera as provas (ClickHouse LOCAL descartável apenas).
# Uso (raiz do repo): bash oteru-dbt/tests/run_chargeback_46_evidence.sh <out_dir> <prefixo>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?out_dir}"; P="${2:?prefixo}"
CH="${CLICKHOUSE_URL:-http://localhost:8123/}"
DBT_BIN="${DBT_BIN:-dbt}"
cd "$ROOT/oteru-dbt"
q() { curl -sf "$CH" --data-binary "$1"; }
hdr() { echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"; echo "clickhouse: $(q 'SELECT version()')"; echo "-----"; }
FIX='chargeback_fixtures: true, chargeback_service_map_seed: chargeback_service_map_fixture_empty'
recon() {
  q "SELECT if(c.cp=1,c.day_utc,d.day_utc) AS day_utc, c.m AS chargeback_micros, d.m AS daily_micros, toInt64(c.m)-toInt64(d.m) AS diff_micros, c.r AS chargeback_requests, d.r AS daily_requests
     FROM (SELECT day_utc, sum(cost_usd_micros) m, sum(requests) r, toUInt8(1) cp FROM otel.fct_cost_chargeback_daily GROUP BY 1) c
     FULL OUTER JOIN (SELECT day_utc, sum(cost_usd_micros) m, sum(requests) r, toUInt8(1) dp FROM otel.fct_cost_daily GROUP BY 1) d ON c.day_utc=d.day_utc
     ORDER BY 1 FORMAT PrettyCompactMonoBlock"
  q "SELECT 'TOTAL' t, (SELECT sum(cost_usd_micros) FROM otel.fct_cost_chargeback_daily) chargeback, (SELECT sum(cost_usd_micros) FROM otel.fct_cost_daily) daily, chargeback - daily AS diff FORMAT PrettyCompactMonoBlock"
  q "SELECT day_utc, team, project, owner_team, attribution_source, cost_usd_micros, requests FROM otel.fct_cost_chargeback_daily ORDER BY 1,2,5 FORMAT PrettyCompactMonoBlock"
  q "SELECT day_utc, team, project, actor_id, cost_usd_micros, requests FROM otel.fct_cost_chargeback_actor_daily ORDER BY 1,2,4 FORMAT PrettyCompactMonoBlock"
}
build() { echo "== dbt build $*"; "$DBT_BIN" build --profiles-dir . "$@" 2>&1; echo "exit=$?"; }

{ hdr; "$DBT_BIN" --version 2>&1 | grep -E 'installed|clickhouse:'; build --full-refresh; } > "$OUT/$P-dbt-build.txt"
{ hdr; echo "== cenário padrão (claude-code mapeado → plataforma)"; recon; } > "$OUT/$P-reconciliation.txt"
{ hdr; q "SELECT name, engine_full, create_table_query FROM system.tables WHERE database='otel' AND name LIKE 'fct_cost_chargeback%' ORDER BY name FORMAT Vertical"; } > "$OUT/$P-system-tables.txt"

{ hdr
  echo "== run 1 (incremental)"; b1="$("$DBT_BIN" build --profiles-dir . 2>&1 | grep 'Done\.')"; echo "$b1"
  t1="$(q "SELECT sum(cost_usd_micros), sum(requests), count() FROM otel.fct_cost_chargeback_daily FORMAT TSV") | $(q "SELECT sum(cost_usd_micros), sum(requests), count() FROM otel.fct_cost_chargeback_actor_daily FORMAT TSV")"
  echo "totais run1 (daily | actor): $t1"
  echo "== run 2 (incremental)"; b2="$("$DBT_BIN" build --profiles-dir . 2>&1 | grep 'Done\.')"; echo "$b2"
  t2="$(q "SELECT sum(cost_usd_micros), sum(requests), count() FROM otel.fct_cost_chargeback_daily FORMAT TSV") | $(q "SELECT sum(cost_usd_micros), sum(requests), count() FROM otel.fct_cost_chargeback_actor_daily FORMAT TSV")"
  echo "totais run2 (daily | actor): $t2"
  [ "$t1" = "$t2" ] && echo "IDEMPOTENTE: PASS" || echo "IDEMPOTENTE: FAIL"
} > "$OUT/$P-idempotency.txt"

{ hdr
  echo "### Cenário A: service_map vazio (sem actor_map) — incremental sobre o cenário padrão (troca de seed na janela)"
  build --vars "{$FIX}" | grep -E 'Done\.|WARN|FAIL|exit='; recon
  echo; echo "### Cenário B: service_map vazio + actor_map populado (pseudônimo de teste)"
  build --vars "{$FIX, chargeback_actor_map_seed: chargeback_actor_map_fixture}" | grep -E 'Done\.|WARN|FAIL|exit=|no_actor_map'; recon
  echo "linhas actor_map no permanente: $(q "SELECT count() FROM otel.fct_cost_chargeback_daily WHERE attribution_source='actor_map'")"
  echo "atribuição em int_cost_attributed:"; q "SELECT attribution_source, team, count(), sum(cost_usd_micros) FROM otel.int_cost_attributed GROUP BY 1,2 ORDER BY 1 FORMAT PrettyCompactMonoBlock"
  echo; echo "### Cenário C: actor_map com hmac:v2: (warn padrão)"
  build --vars "{$FIX, chargeback_actor_map_seed: chargeback_actor_map_fixture_v2}" | grep -E 'Done\.|hmac_version|exit='
  echo "audit (último run):"; q "SELECT run_id, metric_name, day_utc, attribution_source, metric_value FROM otel.audit_cost_runs_chargeback WHERE run_id = (SELECT argMax(run_id, run_completed_at) FROM otel.audit_cost_runs_chargeback) ORDER BY 2,3,4 FORMAT PrettyCompactMonoBlock"
  echo; echo "### Cenário C estrito: chargeback_strict_hmac_version=true (deve dar ERROR)"
  build --vars "{$FIX, chargeback_strict_hmac_version: true, chargeback_actor_map_seed: chargeback_actor_map_fixture_v2}" | grep -E 'Done\.|hmac_version|exit='
  echo; echo "### Volta ao padrão"
  build | grep -E 'Done\.|exit='; recon
} > "$OUT/$P-fixture-scenarios.txt"

{ hdr
  echo "== '@' em colunas String dos novos objetos / regex actor_id"
  q "SELECT 'int_cost_attributed' o, countIf(position(concat(dedup_key,actor_id,service_name,team,project,owner_team),'@')>0) com_arroba, countIf(NOT match(actor_id,'^hmac:v1:[0-9a-f]{64}\$')) fora_regex, count() total FROM otel.int_cost_attributed
     UNION ALL SELECT 'fct_cost_chargeback_daily', countIf(position(concat(team,project,owner_team,toString(attribution_source)),'@')>0), 0, count() FROM otel.fct_cost_chargeback_daily
     UNION ALL SELECT 'fct_cost_chargeback_actor_daily', countIf(position(concat(team,project,actor_id),'@')>0), countIf(NOT match(actor_id,'^hmac:v1:[0-9a-f]{64}\$')), count() FROM otel.fct_cost_chargeback_actor_daily
     UNION ALL SELECT 'chargeback_service_map', countIf(position(concat(service_name,team,project,owner_team),'@')>0), 0, count() FROM otel.chargeback_service_map
     UNION ALL SELECT 'chargeback_actor_map', countIf(position(concat(actor_id,team,project),'@')>0), countIf(NOT match(actor_id,'^hmac:v1:[0-9a-f]{64}\$')), count() FROM otel.chargeback_actor_map
     FORMAT PrettyCompactMonoBlock"
  echo "== grep '@' em seeds/chargeback*"; grep -rn '@' seeds/chargeback_* && echo FAIL || echo "nenhum @ (PASS)"
  echo "== dbt test PII"; "$DBT_BIN" test --profiles-dir . --select test_chargeback_no_pii test_no_pii 2>&1 | grep -E 'PASS|FAIL|Done\.'
} > "$OUT/$P-pii.txt"

{ hdr; echo "== make check-cost-retention (script do Alicerce, não editado)"; (cd "$ROOT" && make check-cost-retention 2>&1); echo "exit=$?"; } > "$OUT/$P-check-cost-retention.txt"
echo "evidências em $OUT/$P-*"
