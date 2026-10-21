#!/usr/bin/env bash
# #48: gera as provas da camada de budget (ClickHouse LOCAL descartável apenas).
# Uso (raiz do repo): bash oteru-dbt/tests/run_budget_48_evidence.sh <out_dir>
# Env: CLICKHOUSE_URL (http, default http://localhost:8123/), CLICKHOUSE_PORT (dbt), DBT_BIN;
#      STALE_CLICKHOUSE_URL/STALE_CLICKHOUSE_PORT: 2º CH local vazio para a prova de stale.
#      Pré-requisito LOCAL (fora do dbt; em ambiente real é do Alicerce, §7.1):
#      usuário oteru_budget_view_definer com SELECT só em fct_budget_consumption_daily.
# Nada de --full-refresh, DROP ou TRUNCATE (o usuário local de teste fica no CH descartável). Nenhum secret: a senha do leitor de
# teste é gerada na hora e só existe na memória deste processo.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?out_dir}"; mkdir -p "$OUT"
CH="${CLICKHOUSE_URL:-http://localhost:8123/}"
DBT_BIN="${DBT_BIN:-dbt}"
SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
cd "$ROOT/oteru-dbt"
q() { curl -sS "$CH" --data-binary "$1"; }
qp() { q "$1 FORMAT PrettyCompactMonoBlock"; }
hdr() { echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"; echo "clickhouse: $(q 'SELECT version()') tz=$(q 'SELECT timezone()')"; echo "-----"; }
FCT_HASH="SELECT count(), sum(cityHash64(day_utc, scope, scope_key, team, project, owner_team, budget_usd_micros, consumed_usd_micros, consumed_period_usd_micros, consumed_milli, warn_milli, breach_milli, budget_status, action_on_breach, last_cost_ts, projected_period_usd_micros)) FROM otel.fct_budget_consumption_daily FORMAT TSV"
VIEW_HASH="SELECT count(), sum(cityHash64(*)) FROM (SELECT * EXCEPT is_stale FROM otel.budget_status_current) FORMAT TSV"

# 1. build
{ hdr; "$DBT_BIN" --version 2>&1 | grep -E 'installed|clickhouse:'
  # Projeto inteiro primeiro: em CH vazio, o seletor do plan sozinho seleciona (indirect
  # selection eager) testes da #46 que leem modelos fora do seletor (fct_cost_daily, actor_daily).
  echo "== dbt build (projeto inteiro, CH novo, sem --full-refresh; regressão #45/#46/#47)"
  "$DBT_BIN" build --profiles-dir . 2>&1; echo "exit=$?"
  echo; echo "== dbt build -s +budget_status_current audit_budget_runs (seletor do plan, sem --full-refresh)"
  "$DBT_BIN" build --profiles-dir . -s +budget_status_current audit_budget_runs 2>&1 | grep -E 'budget|FAIL|ERROR|Done\.'; echo "exit=${PIPESTATUS[0]}"
  echo; echo "== dbt ls budget"; "$DBT_BIN" ls --profiles-dir . -s "budgets+" --resource-type model --resource-type seed 2>&1 | grep -v 'dbt v2\|install\|upgrade'
  echo; echo "== manifest: contract enforced e colunas (sem actor)"
  python3 - <<'PY'
import json
m = json.load(open("target/manifest.json"))
for n in m["nodes"].values():
    if n["resource_type"] == "model" and n["name"] in ("stg_budgets","fct_budget_consumption_daily","budget_status_current","audit_budget_runs"):
        cols = list(n["columns"])
        print(n["name"], "| materialized:", n["config"]["materialized"], "| contract.enforced:", n["config"]["contract"]["enforced"],
              "| sql_security:", n["config"].get("sql_security"), "| definer:", n["config"].get("definer"),
              "| actor-like cols:", [c for c in cols if "actor" in c.lower()])
        print("   ", ", ".join(f"{c}:{n['columns'][c].get('data_type')}" for c in cols))
PY
} > "$OUT/dbt-build-$SHA.txt"

# 2. reconciliação
{ hdr
  echo "== por scope: consumed (fct) vs chargeback no grão (diff != 0 listado; esperado 0 linhas)"
  "$DBT_BIN" test --profiles-dir . -s assert_budget_consumption_reconciles_chargeback assert_budget_global_reconciles_total 2>&1 | grep -E 'PASS|FAIL|Done\.'
  echo "== global vs total diário de fct_cost_chargeback_daily (inclui unassigned)"
  qp "SELECT c.day_utc, c.total AS chargeback_total, f.consumed_usd_micros AS global_consumed, toInt64(f.consumed_usd_micros) - toInt64(c.total) AS diff,
             c.unassigned AS of_which_unassigned
      FROM (SELECT day_utc, sum(cost_usd_micros) total, sumIf(cost_usd_micros, team='unassigned') unassigned FROM otel.fct_cost_chargeback_daily GROUP BY 1) c
      LEFT JOIN (SELECT day_utc, consumed_usd_micros FROM otel.fct_budget_consumption_daily WHERE scope='global') f USING day_utc ORDER BY 1"
  echo "== fct_budget_consumption_daily"
  qp "SELECT day_utc, scope, scope_key, budget_usd_micros, consumed_usd_micros, consumed_period_usd_micros, consumed_milli, budget_status, last_cost_ts FROM otel.fct_budget_consumption_daily ORDER BY 1,2,3"
  echo "== budget_status_current (fixture: team plataforma em breach)"
  qp "SELECT * FROM otel.budget_status_current ORDER BY scope, scope_key"
  echo "== audit_budget_runs (último run)"
  qp "SELECT * FROM otel.audit_budget_runs WHERE run_id = (SELECT argMax(run_id, run_completed_at) FROM otel.audit_budget_runs) ORDER BY scope, scope_key, status"
} > "$OUT/budget-reconcile-$SHA.txt"

# 3. thresholds + Float
{ hdr
  echo "== unit tests (bordas 79,9/80/100%, overflow ~9e15, multi-grão, virada de mês, stale)"
  "$DBT_BIN" test --profiles-dir . -s "test_type:unit" 2>&1 | grep -E 'unit_test|PASS|FAIL|Done\.'
  echo "== testes de contrato S2/S3/CA5"
  "$DBT_BIN" test --profiles-dir . -s assert_no_float_in_budget_contracts assert_budget_status_rule assert_budget_micros_positive_when_budgeted assert_no_actor_columns_in_budget_models 2>&1 | grep -E 'PASS|FAIL|Done\.'
  echo "== system.columns dos objetos de budget"
  qp "SELECT table, name, type FROM system.columns WHERE database='otel' AND table IN ('budgets','stg_budgets','fct_budget_consumption_daily','budget_status_current','audit_budget_runs') ORDER BY table, position"
  echo "colunas Float: $(q "SELECT count() FROM system.columns WHERE database='otel' AND table IN ('budgets','stg_budgets','fct_budget_consumption_daily','budget_status_current','audit_budget_runs') AND type ILIKE '%float%'")"
} > "$OUT/budget-thresholds-$SHA.txt"

# 4. seed negativo: cópia descartável do projeto, schema otel_budget_neg; seed real intacto
NEG="$(mktemp -d)"; trap 'rm -rf "$NEG"' EXIT
cp -r "$ROOT/oteru-dbt/." "$NEG/"; rm -rf "$NEG/target" "$NEG/logs"
python3 - "$NEG" <<'PY'
import sys, re
d = sys.argv[1]
p = f"{d}/dbt_project.yml"; s = open(p).read()
s = re.sub(r"on-run-end:\n(  - .*\n)+", "", s)  # só na cópia: hook de audit não se aplica ao schema de teste
open(p, "w").write(s)
p = f"{d}/profiles.yml"; s = open(p).read().replace("schema: otel", "schema: otel_budget_neg"); open(p, "w").write(s)
PY
REAL_BEFORE="$(sha256sum seeds/budgets.csv | cut -d' ' -f1) $(q 'SELECT count(), groupBitXor(cityHash64(toString(tuple(*)))) FROM otel.budgets FORMAT TSV')"
H='scope,team,project,period,budget_usd_micros,warn_milli,breach_milli,action_on_breach,valid_from,valid_to,owner_team'
AT="$(printf '\x40')"   # '@' gerado em runtime: nenhum '@' commitado em fixture
neg() {
  local name="$1" row="$2" extra="${3:-}"
  printf '%s\n%s\n%s' "$H" "$row" "$extra" | sed '/^$/d' > "$NEG/seeds/budgets.csv"
  echo "### caso: $name"; sed 's/^/    /' "$NEG/seeds/budgets.csv"
  (cd "$NEG" && "$DBT_BIN" build --profiles-dir . -s budgets --indirect-selection=cautious 2>&1 | grep -E ' (FAIL|ERROR|PASS) |Done\.' | sed 's/\x1b\[[0-9;]*m//g')
  echo
}
{ hdr
  echo "schema de teste: otel_budget_neg (cópia temporária do projeto); seed real e otel.budgets não são tocados"
  echo
  neg "action_on_breach=block (deve falhar: só 'alert')" "global,,,month,1000,800,1000,block,2026-10-01,,plataforma"
  neg "warn >= breach" "global,,,month,1000,1000,1000,alert,2026-10-01,,plataforma"
  neg "warn = 0" "global,,,month,1000,0,1000,alert,2026-10-01,,plataforma"
  neg "'@' em team" "team,time${AT}x,,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "slug inválido em project" "project,,Projeto_X,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "budget < 0" "global,,,month,-1,800,1000,alert,2026-10-01,,plataforma"
  neg "scope inválido" "pair,a,x,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "scope incoerente (team com project)" "team,a,x,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "vigência sobreposta + dois global vigentes" "global,,,month,1000,800,1000,alert,2026-10-01,,plataforma" "global,,,month,2000,800,1000,alert,2026-10-15,,plataforma"
  neg "duplicata (scope, team, project, valid_from)" "team,a,,month,1000,800,1000,alert,2026-10-01,2026-10-31,plataforma" "team,a,,month,2000,800,1000,alert,2026-10-01,2026-10-31,plataforma"
  neg "controle válido (deve passar)" "global,,,month,1000,800,1000,alert,2026-10-01,2026-10-14,plataforma" "global,,,month,2000,800,1000,alert,2026-10-15,,plataforma"
  REAL_AFTER="$(sha256sum seeds/budgets.csv | cut -d' ' -f1) $(q 'SELECT count(), groupBitXor(cityHash64(toString(tuple(*)))) FROM otel.budgets FORMAT TSV')"
  echo "seed real antes:  $REAL_BEFORE"; echo "seed real depois: $REAL_AFTER"
  [ "$REAL_BEFORE" = "$REAL_AFTER" ] && echo "SEED REAL INTACTO: PASS" || echo "SEED REAL INTACTO: FAIL"
} > "$OUT/budget-seed-negative-$SHA.txt"

# 5. grants da view (S5) — usuários LOCAIS de teste
PW="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
q "CREATE USER IF NOT EXISTS oteru_budget_reader_localtest IDENTIFIED WITH sha256_password BY '$PW' HOST LOCAL" >/dev/null
q "GRANT SELECT ON otel.budget_status_current TO oteru_budget_reader_localtest" >/dev/null
qr() { curl -sS "$CH" -H "X-ClickHouse-User: oteru_budget_reader_localtest" -H "X-ClickHouse-Key: $PW" --data-binary "$1" 2>&1 | sed -E 's/\(version [^)]*\)//'; }
{ hdr
  echo "leitor de teste local: oteru_budget_reader_localtest (só SELECT na view; senha gerada em runtime, não registrada)"
  echo "o role real (oteru_budget_reader / oteru_budget_alerts_reader) e seus grants são do Alicerce (§7.1/§7.4/§7.12)"
  echo "== 1. SELECT count() FROM budget_status_current (esperado ok)"; qr "SELECT count() FROM otel.budget_status_current"
  echo "== 2a. SELECT * FROM fct_budget_consumption_daily (esperado 497)"; qr "SELECT * FROM otel.fct_budget_consumption_daily LIMIT 1"
  echo "== 2b. SELECT * FROM fct_cost_chargeback_daily (esperado 497)"; qr "SELECT * FROM otel.fct_cost_chargeback_daily LIMIT 1"
  echo "== 2c. SELECT * FROM int_cost_attributed (esperado 497)"; qr "SELECT * FROM otel.int_cost_attributed LIMIT 1"
  echo "== 3. SHOW CREATE VIEW budget_status_current"; q "SHOW CREATE VIEW otel.budget_status_current FORMAT TSVRaw"
  echo; echo "contém 'SQL SECURITY DEFINER': $(q 'SHOW CREATE VIEW otel.budget_status_current FORMAT TSVRaw' | grep -c 'SQL SECURITY DEFINER')"
  echo "contém 'DEFINER = oteru_budget_view_definer': $(q 'SHOW CREATE VIEW otel.budget_status_current FORMAT TSVRaw' | grep -c 'DEFINER = oteru_budget_view_definer')"
  echo "== 4. SHOW GRANTS FOR oteru_budget_view_definer"; q "SHOW GRANTS FOR oteru_budget_view_definer FORMAT TSVRaw"
  echo "== SHOW GRANTS FOR oteru_budget_reader_localtest"; q "SHOW GRANTS FOR oteru_budget_reader_localtest FORMAT TSVRaw"
} > "$OUT/budget-view-grants-$SHA.txt"

# 6. idempotência
{ hdr
  echo "== run 1 (incremental)"; "$DBT_BIN" build --profiles-dir . -s +budget_status_current audit_budget_runs 2>&1 | grep 'Done\.'
  f1="$(q "$FCT_HASH")"; v1="$(q "$VIEW_HASH")"; echo "fct (count, hash): $f1 | view: $v1"
  echo "== run 2 (incremental)"; "$DBT_BIN" build --profiles-dir . -s +budget_status_current audit_budget_runs 2>&1 | grep 'Done\.'
  f2="$(q "$FCT_HASH")"; v2="$(q "$VIEW_HASH")"; echo "fct (count, hash): $f2 | view: $v2"
  echo "(loaded_at fora do hash; audit_budget_runs é append por run por desenho)"
  echo "runs em audit_budget_runs: $(q 'SELECT uniqExact(run_id) FROM otel.audit_budget_runs')"
  [ "$f1" = "$f2" ] && [ "$v1" = "$v2" ] && echo "IDEMPOTENTE: PASS" || echo "IDEMPOTENTE: FAIL"
} > "$OUT/budget-idempotency-$SHA.txt"

# 7. stale (S1): CH LOCAL SEPARADO e vazio (STALE_CLICKHOUSE_URL / STALE_CLICKHOUSE_PORT), fixture com
#    último event_ts = T = agora - 28h; build com o limite PADRÃO (26h), sem esperar.
if [ -n "${STALE_CLICKHOUSE_URL:-}" ]; then
  qs() { curl -sS "$STALE_CLICKHOUSE_URL" --data-binary "$1"; }
  sb() { CLICKHOUSE_PORT="${STALE_CLICKHOUSE_PORT:?}" "$DBT_BIN" build --profiles-dir . 2>&1 | grep -E 'Done\.|Budget audit'; }
  sv() { qs "SELECT scope, scope_key, as_of_day, as_of_ts, stale_threshold_hours, is_stale, budget_status FROM otel.budget_status_current ORDER BY 1,2 FORMAT PrettyCompactMonoBlock"; }
  { echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"; echo "clickhouse (stale): $(qs 'SELECT version()') tz=$(qs 'SELECT timezone()')"; echo "-----"
    echo "linhas de custo antes do load (esperado 0): $(qs 'SELECT count() FROM otel.stg_api_request')"
    python3 tests/load_budget_stale_fixture.py "$STALE_CLICKHOUSE_URL" 28
    T="$(qs "SELECT max(event_ts) FROM otel.stg_api_request")"; echo "T = max(event_ts) da fixture: $T | now: $(qs "SELECT now64(3,'UTC')")"
    echo "vars: budget_stale_hours_daily = $(grep -E '^  budget_stale_hours_daily:' dbt_project.yml | awk '{print $2}') (padrão, não alterado)"
    echo "== build 1 (T+28h, primeiro processamento do custo)"; sb; sv
    A1="$(qs 'SELECT max(as_of_ts) FROM otel.budget_status_current')"
    echo "== build 2 (T+28h, run bem-sucedido SEM custo novo)"; sb; sv
    A2="$(qs 'SELECT max(as_of_ts) FROM otel.budget_status_current')"
    echo "as_of_ts build1=$A1 build2=$A2 T=$T"
    [ "$A1" = "$T" ] && [ "$A2" = "$T" ] && echo "as_of_ts = T antes e depois: PASS" || echo "as_of_ts = T: FAIL"
    [ "$(qs 'SELECT min(is_stale) FROM otel.budget_status_current')" = "true" ] && echo "is_stale=true em todas as linhas (limite 26h): PASS" || echo "is_stale=true: FAIL"
    echo "audit_budget_runs: $(qs "SELECT groupArray((run_status, n)) FROM (SELECT run_id, any(run_status) run_status, count() n FROM otel.audit_budget_runs GROUP BY run_id ORDER BY min(run_completed_at))")"
  } > "$OUT/budget-stale-$SHA.txt"
fi

# 8. audit_budget_runs: run que falha é marcado run_status='error' (definer inexistente => view falha)
{ hdr
  echo "== run com falha forçada (budget_view_definer inexistente)"
  "$DBT_BIN" build --profiles-dir . --vars '{budget_view_definer: oteru_budget_definer_inexistente}' 2>&1 | grep -E ' ERROR creating|Done\.|Budget audit'
  echo "== run normal"
  "$DBT_BIN" build --profiles-dir . 2>&1 | grep -E 'Done\.|Budget audit'
  qp "SELECT run_id, any(run_status) AS run_status, uniqExact(run_status) AS n_status, count() AS rows, min(run_completed_at) AS at FROM otel.audit_budget_runs GROUP BY run_id ORDER BY at"
  echo "último run: $(q "SELECT any(run_status) FROM otel.audit_budget_runs WHERE run_id = (SELECT argMax(run_id, run_completed_at) FROM otel.audit_budget_runs)")"
  echo "penúltimo run: $(q "SELECT run_status FROM (SELECT run_id, any(run_status) run_status, max(run_completed_at) t FROM otel.audit_budget_runs GROUP BY run_id ORDER BY t DESC LIMIT 1 OFFSET 1)")"
  "$DBT_BIN" test --profiles-dir . -s audit_budget_runs 2>&1 | grep -E ' PASS | FAIL | ERROR |Done\.'
} > "$OUT/budget-audit-run-status-$SHA.txt"
echo "provas em $OUT"
