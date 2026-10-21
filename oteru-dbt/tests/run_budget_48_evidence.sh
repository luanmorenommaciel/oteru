#!/usr/bin/env bash
# #48: gera as provas da camada de budget (ClickHouse LOCAL descartável apenas).
# Uso (raiz do repo): bash oteru-dbt/tests/run_budget_48_evidence.sh <out_dir>
# Env: CLICKHOUSE_URL (http, default http://localhost:8123/), CLICKHOUSE_PORT (dbt), DBT_BIN;
#      STALE_CLICKHOUSE_URL/STALE_CLICKHOUSE_PORT: 2º CH local vazio (stale + A3); CH_BIN: binário
#      clickhouse (testes da Vigia); GITLEAKS_BIN opcional; PYTEST_PYTHON (python com pytest);
#      OTERU_BUDGET_DEFINER_PASSWORD opcional (só env).
#      Roles/usuários: scripts/bootstrap_budget_roles.sh (Alicerce) roda ANTES de qualquer dbt build,
#      nos dois CH; se falhar, o script aborta.
# Nada de --full-refresh, DROP ou TRUNCATE. Nenhum secret: senhas só via env, nunca impressas.
# Modo estrito: qualquer passo inesperado aborta com mensagem clara e exit != 0. Passos que
# DEVEM falhar (seeds inválidos, run com falha forçada, 497) são tratados explicitamente.
set -euo pipefail
die() { echo "ABORT run_budget_48_evidence: $*" >&2; exit 1; }
trap 'rc=$?; [ $rc -eq 0 ] || echo "ABORT run_budget_48_evidence: passo falhou (linha $LINENO, exit $rc)" >&2' EXIT
FAILS=0
check() { if eval "$2"; then echo "$1: PASS"; else echo "$1: FAIL"; FAILS=$((FAILS+1)); fi; }
section_ok() { [ "$FAILS" -eq 0 ] || die "verificação falhou em $1 (ver $2)"; }
# dbt que deve passar: guarda o log completo, mostra só o filtro, aborta se exit != 0
dbt_ok() { local log; log="$(mktemp)"; if "$DBT_BIN" "$@" >"$log" 2>&1; then grep -E "${DBT_FILTER:-Done\.|Budget audit}" "$log" | sed 's/\x1b\[[0-9;]*m//g' || true; rm -f "$log"; else sed 's/\x1b\[[0-9;]*m//g' "$log" | tail -40; rm -f "$log"; die "dbt $* falhou"; fi; }
# dbt que DEVE falhar (exit != 0 esperado)
dbt_expect_fail() { local log rc=0; log="$(mktemp)"; "$DBT_BIN" "$@" >"$log" 2>&1 || rc=$?; grep -E "${DBT_FILTER:-Done\.|Budget audit}" "$log" | sed 's/\x1b\[[0-9;]*m//g' || true; rm -f "$log"; echo "exit=$rc (esperado != 0)"; [ "$rc" -ne 0 ]; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${1:?out_dir}"; mkdir -p "$OUT"
CH="${CLICKHOUSE_URL:-http://localhost:8123/}"
DBT_BIN="${DBT_BIN:-dbt}"
SHA="$(git -C "$ROOT" rev-parse --short HEAD)"
cd "$ROOT/oteru-dbt"
q() { curl -sS -f "$CH" --data-binary "$1" || die "query falhou: ${1:0:80}"; }
qp() { q "$1 FORMAT PrettyCompactMonoBlock"; }
hdr() { echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"; echo "clickhouse: $(q 'SELECT version()') tz=$(q 'SELECT timezone()')"; echo "-----"; }
FCT_HASH="SELECT count(), sum(cityHash64(day_utc, scope, scope_key, team, project, owner_team, budget_usd_micros, consumed_usd_micros, consumed_period_usd_micros, consumed_milli, warn_milli, breach_milli, budget_status, action_on_breach, last_cost_ts, projected_period_usd_micros)) FROM otel.fct_budget_consumption_daily FORMAT TSV"
VIEW_HASH="SELECT count(), sum(cityHash64(*)) FROM (SELECT * EXCEPT is_stale FROM otel.budget_status_current) FORMAT TSV"

# 0. Alicerce bootstrap (definer + alerts_reader) BEFORE any dbt build.
# Saída do bootstrap (stdout+stderr) vai para bootstrap-<sha>.txt, coberta pelo grep de senha.
BOOT="$OUT/bootstrap-$SHA.txt"
{ echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"
  echo "OTERU_BUDGET_DEFINER_PASSWORD definida no ambiente: $([ -n "${OTERU_BUDGET_DEFINER_PASSWORD:-}" ] && echo sim || echo não) (valor nunca impresso)"
  echo "== bootstrap_budget_roles.sh --require ($CH)"; } > "$BOOT"
CLICKHOUSE_URL="$CH" bash "$ROOT/scripts/bootstrap_budget_roles.sh" --require >>"$BOOT" 2>&1 || { cat "$BOOT" >&2; die "bootstrap de roles (Alicerce) falhou em $CH"; }
if [ -n "${STALE_CLICKHOUSE_URL:-}" ]; then
  echo "== bootstrap_budget_roles.sh --require ($STALE_CLICKHOUSE_URL)" >> "$BOOT"
  CLICKHOUSE_URL="$STALE_CLICKHOUSE_URL" bash "$ROOT/scripts/bootstrap_budget_roles.sh" --require >>"$BOOT" 2>&1 || { cat "$BOOT" >&2; die "bootstrap de roles (Alicerce) falhou em $STALE_CLICKHOUSE_URL"; }
fi
echo "== SHOW USERS/ROLES" >> "$BOOT"; q "SELECT name, auth_type FROM system.users WHERE name LIKE 'oteru_budget%' FORMAT TSV" >> "$BOOT"; q "SELECT name FROM system.roles WHERE name LIKE 'oteru_budget%' FORMAT TSV" >> "$BOOT"

# 1. build
{ hdr; "$DBT_BIN" --version 2>&1 | grep -E 'installed|clickhouse:'
  # Projeto inteiro primeiro: em CH vazio, o seletor do plan sozinho seleciona (indirect
  # selection eager) testes da #46 que leem modelos fora do seletor (fct_cost_daily, actor_daily).
  echo "== dbt build (projeto inteiro, CH novo, sem --full-refresh; regressão #45/#46/#47)"
  DBT_FILTER='.' dbt_ok build --profiles-dir .; echo "exit=0"
  echo; echo "== dbt build -s +budget_status_current audit_budget_runs (seletor do plan, sem --full-refresh)"
  DBT_FILTER='budget|FAIL|ERROR|Done\.' dbt_ok build --profiles-dir . -s +budget_status_current audit_budget_runs; echo "exit=0"
  echo; echo "== dbt ls budget"; DBT_FILTER='^[a-z]' dbt_ok ls --profiles-dir . -s "budgets+" --resource-type model --resource-type seed
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

# 2. reconciliação (inclui cenário com custo 'unassigned')
RECON_GLOBAL="SELECT c.day_utc, c.total AS chargeback_total, f.consumed_usd_micros AS global_consumed, toInt64(f.consumed_usd_micros) - toInt64(c.total) AS diff, c.unassigned AS of_which_unassigned
      FROM (SELECT day_utc, sum(cost_usd_micros) total, sumIf(cost_usd_micros, team='unassigned') unassigned FROM otel.fct_cost_chargeback_daily GROUP BY 1) c
      LEFT JOIN (SELECT day_utc, consumed_usd_micros FROM otel.fct_budget_consumption_daily WHERE scope='global') f USING day_utc ORDER BY 1"
RECON_DIFF="SELECT count() FROM (SELECT c.day_utc, c.total, f.m FROM (SELECT day_utc, sum(cost_usd_micros) total FROM otel.fct_cost_chargeback_daily GROUP BY 1) c LEFT JOIN (SELECT day_utc, sum(consumed_usd_micros) m, count() n FROM otel.fct_budget_consumption_daily WHERE scope='global' GROUP BY 1) f USING day_utc WHERE f.n != 1 OR toInt64(f.m) != toInt64(c.total))"
FIXV='{chargeback_fixtures: true, chargeback_service_map_seed: chargeback_service_map_fixture_empty}'
{ hdr
  echo "== cenário padrão: testes dbt de reconciliação por scope e global exato"
  DBT_FILTER=' PASS | FAIL | ERROR |Done\.' dbt_ok test --profiles-dir . -s assert_budget_consumption_reconciles_chargeback assert_budget_global_reconciles_total
  echo "== global vs total diário de fct_cost_chargeback_daily"; qp "$RECON_GLOBAL"
  check "global = total (padrão), dias com diferença = 0" '[ "$(q "$RECON_DIFF")" = "0" ]'
  echo; echo "== cenário 'unassigned': service_map vazio (fixture #46) => todo custo vira 'unassigned'"
  dbt_ok build --profiles-dir . --vars "$FIXV"
  qp "$RECON_GLOBAL"
  check "há custo unassigned no chargeback" '[ "$(q "SELECT sumIf(cost_usd_micros, team='"'"'unassigned'"'"') > 0 FROM otel.fct_cost_chargeback_daily")" = "1" ]'
  check "global inclui unassigned: global = total exato" '[ "$(q "$RECON_DIFF")" = "0" ]'
  check "cap team plataforma não sobe com unassigned (consumo do dia = 0)" '[ "$(q "SELECT max(consumed_usd_micros) FROM otel.fct_budget_consumption_daily WHERE scope='"'"'team'"'"' AND day_utc = (SELECT max(day_utc) FROM otel.fct_cost_chargeback_daily)")" = "0" ]'
  DBT_FILTER=' PASS | FAIL | ERROR |Done\.' dbt_ok test --profiles-dir . -s assert_budget_consumption_reconciles_chargeback assert_budget_global_reconciles_total
  echo; echo "== volta ao cenário padrão"; dbt_ok build --profiles-dir .
  echo "== fct_budget_consumption_daily"
  qp "SELECT day_utc, scope, scope_key, budget_usd_micros, consumed_usd_micros, consumed_period_usd_micros, consumed_milli, budget_status, last_cost_ts FROM otel.fct_budget_consumption_daily ORDER BY 1,2,3"
  echo "== budget_status_current (fixture: team plataforma em breach)"
  qp "SELECT * FROM otel.budget_status_current ORDER BY scope, scope_key"
  check "team plataforma em breach" '[ "$(q "SELECT budget_status FROM otel.budget_status_current WHERE scope='"'"'team'"'"' AND scope_key='"'"'plataforma'"'"'")" = "breach" ]'
} > "$OUT/budget-reconcile-$SHA.txt"
section_ok reconcile "$OUT/budget-reconcile-$SHA.txt"

# 3. thresholds + Float
{ hdr
  echo "== unit tests (bordas 79,9/80/100%, overflow ~9e15, multi-grão, virada de mês, troca no mês, stale)"
  DBT_FILTER='unit_test| PASS | FAIL |Done\.' dbt_ok test --profiles-dir . -s "test_type:unit"
  echo "== testes de contrato S2/S3/CA5"
  DBT_FILTER=' PASS | FAIL |Done\.' dbt_ok test --profiles-dir . -s assert_no_float_in_budget_contracts assert_budget_status_rule assert_budget_micros_positive_when_budgeted assert_no_actor_columns_in_budget_models
  echo "== system.columns dos objetos de budget"
  qp "SELECT table, name, type FROM system.columns WHERE database='otel' AND table IN ('budgets','stg_budgets','fct_budget_consumption_daily','budget_status_current','audit_budget_runs') ORDER BY table, position"
  NF="$(q "SELECT count() FROM system.columns WHERE database='otel' AND table IN ('budgets','stg_budgets','fct_budget_consumption_daily','budget_status_current','audit_budget_runs') AND type ILIKE '%float%'")"
  echo "colunas Float: $NF"; check "sem Float" '[ "$NF" = "0" ]'
} > "$OUT/budget-thresholds-$SHA.txt"
section_ok thresholds "$OUT/budget-thresholds-$SHA.txt"

# 4. seed negativo: cópia descartável do projeto, schema otel_budget_neg; seed real intacto
NEG="$(mktemp -d)"
trap 'rc=$?; rm -rf "$NEG"; [ $rc -eq 0 ] || echo "ABORT run_budget_48_evidence: passo falhou (exit $rc)" >&2' EXIT
cp -r "$ROOT/oteru-dbt/." "$NEG/"; rm -rf "$NEG/target" "$NEG/logs"
python3 - "$NEG" <<'PY'
import sys, re
d = sys.argv[1]
p = f"{d}/dbt_project.yml"; s = open(p).read()
s = re.sub(r"on-run-end:\n(  - .*\n)+", "", s)  # só na cópia: hooks de audit não se aplicam ao schema de teste
open(p, "w").write(s)
p = f"{d}/profiles.yml"; s = open(p).read().replace("schema: otel", "schema: otel_budget_neg"); open(p, "w").write(s)
PY
REAL_BEFORE="$(sha256sum seeds/budgets.csv | cut -d' ' -f1) $(q 'SELECT count(), groupBitXor(cityHash64(toString(tuple(*)))) FROM otel.budgets FORMAT TSV')"
H='scope,team,project,period,budget_usd_micros,warn_milli,breach_milli,action_on_breach,valid_from,valid_to,owner_team'
AT="$(printf '\x40')"   # '@' gerado em runtime: nenhum '@' commitado em fixture
# neg <nome> <esperado: fail|pass> <linha> [linha2]
neg() {
  local name="$1" want="$2" row="$3" extra="${4:-}" rc=0 log
  printf '%s\n%s\n%s' "$H" "$row" "$extra" | sed '/^$/d' > "$NEG/seeds/budgets.csv"
  echo "### caso: $name (esperado: $want)"; sed 's/^/    /' "$NEG/seeds/budgets.csv"
  log="$(mktemp)"
  (cd "$NEG" && "$DBT_BIN" build --profiles-dir . -s budgets --indirect-selection=cautious) >"$log" 2>&1 || rc=$?
  sed 's/\x1b\[[0-9;]*m//g' "$log" | grep -E ' (FAIL|ERROR) |Done\.' || true; rm -f "$log"
  if [ "$want" = fail ]; then check "  $name rejeitado" '[ "$rc" -ne 0 ]'; else check "  $name aceito" '[ "$rc" -eq 0 ]'; fi
  echo
}
{ hdr
  echo "schema de teste: otel_budget_neg (cópia temporária do projeto); seed real e otel.budgets não são tocados"
  echo
  neg "action_on_breach=block (só 'alert')" fail "global,,,month,1000,800,1000,block,2026-10-01,,plataforma"
  neg "warn >= breach" fail "global,,,month,1000,1000,1000,alert,2026-10-01,,plataforma"
  neg "warn = 0" fail "global,,,month,1000,0,1000,alert,2026-10-01,,plataforma"
  neg "'@' em team" fail "team,time${AT}x,,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "slug inválido em project" fail "project,,Projeto_X,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "budget < 0" fail "global,,,month,-1,800,1000,alert,2026-10-01,,plataforma"
  neg "scope inválido" fail "pair,a,x,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "scope incoerente (team com project)" fail "team,a,x,month,1000,800,1000,alert,2026-10-01,,plataforma"
  neg "vigência sobreposta + dois global vigentes" fail "global,,,month,1000,800,1000,alert,2026-10-01,,plataforma" "global,,,month,2000,800,1000,alert,2026-10-15,,plataforma"
  neg "duplicata (scope, team, project, valid_from)" fail "team,a,,month,1000,800,1000,alert,2026-10-01,2026-10-31,plataforma" "team,a,,month,2000,800,1000,alert,2026-10-01,2026-10-31,plataforma"
  neg "controle válido" pass "global,,,month,1000,800,1000,alert,2026-10-01,2026-10-14,plataforma" "global,,,month,2000,800,1000,alert,2026-10-15,,plataforma"
  REAL_AFTER="$(sha256sum seeds/budgets.csv | cut -d' ' -f1) $(q 'SELECT count(), groupBitXor(cityHash64(toString(tuple(*)))) FROM otel.budgets FORMAT TSV')"
  echo "seed real antes:  $REAL_BEFORE"; echo "seed real depois: $REAL_AFTER"
  check "SEED REAL INTACTO" '[ "$REAL_BEFORE" = "$REAL_AFTER" ]'
  echo; echo "== guard do seed (Alicerce): scripts/check_chargeback_seeds.py"
  (cd "$ROOT" && python3 scripts/check_chargeback_seeds.py) || die "guard do seed (Alicerce) falhou"
} > "$OUT/budget-seed-negative-$SHA.txt"
section_ok seed-negative "$OUT/budget-seed-negative-$SHA.txt"

# 5. grants da view (S5) com os roles REAIS do bootstrap do Alicerce
AR_USER=oteru_budget_alerts_proof
q "CREATE USER IF NOT EXISTS $AR_USER IDENTIFIED WITH no_password HOST LOCAL" >/dev/null
q "GRANT oteru_budget_alerts_reader TO $AR_USER" >/dev/null
qa() { curl -sS "$CH" -H "X-ClickHouse-User: $AR_USER" --data-binary "$1" 2>&1 | sed -E 's/\(version [^)]*\)//'; }
allowed() { local out; out="$(qa "SELECT $2 FROM otel.$1 LIMIT 1 FORMAT Null")"; echo "  SELECT $2 FROM $1 -> ${out:-ok}"; check "    allowed $1.$2" '[ -z "$out" ]'; }
denied() { local out; out="$(qa "$1")"; echo "  $1 -> $(echo "$out" | head -1 | cut -c1-160)"; check "    denied (497)" 'echo "$out" | grep -q "Code: 497"'; }
{ hdr
  echo "role real: oteru_budget_alerts_reader (scripts/bootstrap_budget_roles.sql, Alicerce), concedido ao usuário local $AR_USER (sem senha, HOST LOCAL)"
  echo "== allowed: cada coluna de audit_budget_runs"
  for c in run_id run_completed_at run_status scope scope_key team project status count; do allowed audit_budget_runs "$c"; done
  echo "== allowed: colunas permitidas de budget_status_current"
  for c in scope scope_key team project period_start as_of_ts is_stale budget_status action_on_breach; do allowed budget_status_current "$c"; done
  echo "== allowed: colunas permitidas de audit_cost_runs"
  for c in run_id run_completed_at run_status events_dropped_late; do allowed audit_cost_runs "$c"; done
  echo "== denied"
  for c in owner_team budget_usd_micros consumed_period_usd_micros consumed_milli warn_milli breach_milli as_of_day stale_threshold_hours; do denied "SELECT $c FROM otel.budget_status_current LIMIT 1"; done
  denied "SELECT rows_processed FROM otel.audit_cost_runs LIMIT 1"
  denied "SELECT * FROM otel.fct_budget_consumption_daily LIMIT 1"
  denied "SELECT * FROM otel.fct_cost_chargeback_daily LIMIT 1"
  denied "SELECT * FROM otel.fct_cost_daily LIMIT 1"
  denied "SELECT * FROM otel.int_cost_attributed LIMIT 1"
  denied "SELECT * FROM otel.budgets LIMIT 1"
  denied "SELECT * FROM otel.otel_logs LIMIT 1"
  denied "INSERT INTO otel.audit_budget_runs SELECT * FROM otel.audit_budget_runs LIMIT 0"
  echo "== SHOW GRANTS FOR oteru_budget_alerts_reader"; G="$(q "SHOW GRANTS FOR oteru_budget_alerts_reader FORMAT TSVRaw")"; echo "$G"
  check "alerts_reader inclui period_start" 'echo "$G" | grep -q period_start'
  check "alerts_reader inclui run_status (audit_cost_runs)" 'echo "$G" | grep "audit_cost_runs" | grep -q run_status'
  echo "== SHOW GRANTS FOR $AR_USER"; q "SHOW GRANTS FOR $AR_USER FORMAT TSVRaw"
  echo "== SHOW GRANTS FOR oteru_budget_view_definer"; D="$(q "SHOW GRANTS FOR oteru_budget_view_definer FORMAT TSVRaw")"; echo "$D"
  check "definer só SELECT em fct_budget_consumption_daily" '[ "$(echo "$D" | wc -l)" = "1" ] && echo "$D" | grep -q "GRANT SELECT(.*) ON otel.fct_budget_consumption_daily"'
  echo "== SHOW CREATE VIEW budget_status_current"; V="$(q "SHOW CREATE VIEW otel.budget_status_current FORMAT TSVRaw")"; echo "$V"
  check "view SQL SECURITY DEFINER" 'echo "$V" | grep -q "DEFINER = oteru_budget_view_definer SQL SECURITY DEFINER"'
  echo; echo "== scripts/prove_budget_view_grants.sh (Alicerce, live 2b)"
  (cd "$ROOT" && CLICKHOUSE_URL="$CH" bash scripts/prove_budget_view_grants.sh) || die "prove_budget_view_grants.sh (Alicerce) falhou"
  echo; echo "== pytest tests/test_budget_grants.py (live, sem skip)"
  PYLOG="$(mktemp)"
  (cd "$ROOT" && CLICKHOUSE_URL="$CH" "${PYTEST_PYTHON:-python3}" -m pytest -v -rs tests/test_budget_grants.py) >"$PYLOG" 2>&1 || { cat "$PYLOG"; die "tests/test_budget_grants.py (Alicerce) falhou"; }
  cat "$PYLOG"
  check "pytest sem skip" '! grep -qiE "skipped|SKIP" "$PYLOG"'
  rm -f "$PYLOG"
} > "$OUT/budget-view-grants-$SHA.txt"
section_ok view-grants "$OUT/budget-view-grants-$SHA.txt"

# 6. idempotência
{ hdr
  echo "== run 1 (incremental)"; dbt_ok build --profiles-dir . -s +budget_status_current audit_budget_runs
  f1="$(q "$FCT_HASH")"; v1="$(q "$VIEW_HASH")"; echo "fct (count, hash): $f1 | view: $v1"
  echo "== run 2 (incremental)"; dbt_ok build --profiles-dir . -s +budget_status_current audit_budget_runs
  f2="$(q "$FCT_HASH")"; v2="$(q "$VIEW_HASH")"; echo "fct (count, hash): $f2 | view: $v2"
  echo "(loaded_at fora do hash; audit_budget_runs é append por run por desenho)"
  check "IDEMPOTENTE" '[ "$f1" = "$f2" ] && [ "$v1" = "$v2" ]'
} > "$OUT/budget-idempotency-$SHA.txt"
section_ok idempotency "$OUT/budget-idempotency-$SHA.txt"

# 7. audit_budget_runs no CH principal — controle: falha logo após um success NÃO dispara A3
A3_SQL="$(cat "$ROOT/ops/alerts/48/budget_run_missing.sql")"
{ hdr
  echo "== run normal (success)"; dbt_ok build --profiles-dir .
  echo "== run com falha forçada (budget_view_definer inexistente => view falha)"
  dbt_expect_fail build --profiles-dir . --vars '{budget_view_definer: oteru_budget_definer_inexistente}' || die "run com falha forçada não falhou"
  qp "SELECT run_id, any(run_status) AS rs, uniqExact(run_status) AS n_status, count() AS rows, min(run_completed_at) AS at FROM otel.audit_budget_runs GROUP BY run_id ORDER BY at"
  LAST="$(q "SELECT any(run_status) FROM otel.audit_budget_runs WHERE run_id = (SELECT argMax(run_id, run_completed_at) FROM otel.audit_budget_runs)")"
  check "último run gravado com run_status='error'" '[ "$LAST" = "error" ]'
  echo "== A3 (ops/alerts/48/budget_run_missing.sql, Vigia) — controle: success recente"
  A3OUT="$(q "$A3_SQL")"; echo "A3 linhas: $(printf '%s' "$A3OUT" | grep -c . || true) [$A3OUT]"
  check "A3 NÃO dispara (success < 26h)" '[ -z "$A3OUT" ]'
  echo "== volta: run normal"; dbt_ok build --profiles-dir .
  DBT_FILTER=' PASS | FAIL | ERROR |Done\.' dbt_ok test --profiles-dir . -s audit_budget_runs
} > "$OUT/budget-audit-run-status-$SHA.txt"
section_ok audit-run-status "$OUT/budget-audit-run-status-$SHA.txt"

# 8. stale (S1) + A3 no CH SEPARADO e vazio (STALE_CLICKHOUSE_URL / STALE_CLICKHOUSE_PORT)
if [ -n "${STALE_CLICKHOUSE_URL:-}" ]; then
  qs() { curl -sS -f "$STALE_CLICKHOUSE_URL" --data-binary "$1" || die "query (stale) falhou: ${1:0:80}"; }
  sb() { CLICKHOUSE_PORT="${STALE_CLICKHOUSE_PORT:?}" dbt_ok build --profiles-dir . "$@"; }
  sv() { qs "SELECT scope, scope_key, as_of_day, as_of_ts, stale_threshold_hours, is_stale, budget_status FROM otel.budget_status_current ORDER BY 1,2 FORMAT PrettyCompactMonoBlock"; }
  { echo "tested_sha: $(git -C "$ROOT" rev-parse HEAD)"; echo "utc: $(date -u +%FT%TZ)"; echo "clickhouse (stale): $(qs 'SELECT version()') tz=$(qs 'SELECT timezone()')"; echo "-----"
    N0="$(qs 'SELECT count() FROM otel.stg_api_request')"; echo "linhas de custo antes do load (esperado 0): $N0"
    [ "$N0" = "0" ] || die "CH de stale não está vazio"
    python3 tests/load_budget_stale_fixture.py "$STALE_CLICKHOUSE_URL" 28
    T="$(qs "SELECT max(event_ts) FROM otel.stg_api_request")"; echo "T = max(event_ts) da fixture: $T | now: $(qs "SELECT now64(3,'UTC')")"
    echo "vars: budget_stale_hours_daily = $(grep -E '^  budget_stale_hours_daily:' dbt_project.yml | awk '{print $2}') (padrão, não alterado)"
    echo "== build 1 (T+28h, primeiro processamento do custo)"; sb; sv
    A1="$(qs 'SELECT max(as_of_ts) FROM otel.budget_status_current')"
    echo "== build 2 (T+28h, run bem-sucedido SEM custo novo)"; sb; sv
    A2="$(qs 'SELECT max(as_of_ts) FROM otel.budget_status_current')"
    echo "as_of_ts build1=$A1 build2=$A2 T=$T"
    check "as_of_ts = T antes e depois" '[ "$A1" = "$T" ] && [ "$A2" = "$T" ]'
    check "is_stale=true em todas as linhas (limite 26h)" '[ "$(qs "SELECT min(is_stale) FROM otel.budget_status_current")" = "true" ]'
    echo
    echo "== A3: o único success vira antigo (> 26h) — fixture no CH descartável (mutation local)"
    # run_completed_at é chave de ordenação (não aceita UPDATE): copia os success com
    # run_completed_at = agora - 30h e apaga (lightweight DELETE, só neste CH descartável) os recentes.
    qs "INSERT INTO otel.audit_budget_runs SELECT * REPLACE (now64(3, 'UTC') - INTERVAL 30 HOUR AS run_completed_at) FROM otel.audit_budget_runs WHERE run_status = 'success'" >/dev/null
    qs "DELETE FROM otel.audit_budget_runs WHERE run_status = 'success' AND run_completed_at > now64(3, 'UTC') - INTERVAL 26 HOUR" >/dev/null
    qs "SELECT run_id, any(run_status) AS rs, max(run_completed_at) AS at FROM otel.audit_budget_runs GROUP BY run_id ORDER BY at FORMAT PrettyCompactMonoBlock"
    echo "== run com falha forçada"
    CLICKHOUSE_PORT="$STALE_CLICKHOUSE_PORT" dbt_expect_fail build --profiles-dir . --vars '{budget_view_definer: oteru_budget_definer_inexistente}' || die "run com falha forçada (stale) não falhou"
    qs "SELECT run_id, any(run_status) AS rs, max(run_completed_at) AS at FROM otel.audit_budget_runs GROUP BY run_id ORDER BY at FORMAT PrettyCompactMonoBlock"
    LASTS="$(qs "SELECT any(run_status) FROM otel.audit_budget_runs WHERE run_id = (SELECT argMax(run_id, run_completed_at) FROM otel.audit_budget_runs)")"
    check "run com falha gravado com run_status='error'" '[ "$LASTS" = "error" ]'
    A3S="$(qs "$A3_SQL")"; N3="$(printf '%s' "$A3S" | grep -c . || true)"
    echo "A3 (ops/alerts/48/budget_run_missing.sql): $N3 linha(s) [$A3S]"
    check "A3 dispara com exatamente 1 linha (último success > 26h, só error recente)" '[ "$N3" = "1" ]'
  } > "$OUT/budget-stale-$SHA.txt"
  section_ok stale "$OUT/budget-stale-$SHA.txt"
fi

# 9. alertas da Vigia, PII, gitleaks, grep de senha
{ hdr
  echo "== CH=$CH_BIN bash ops/alerts/48/run_alert_tests.sh (Vigia)"
  if [ -n "${CH_BIN:-}" ]; then
    (cd "$ROOT" && CH="$CH_BIN" bash ops/alerts/48/run_alert_tests.sh) || die "ops/alerts/48/run_alert_tests.sh (Vigia) falhou"
  else die "CH_BIN (binário clickhouse) não definido para os testes da Vigia"; fi
  echo; echo "== make pii-guard (inclui guard do seed)"; (cd "$ROOT" && make pii-guard) || die "make pii-guard falhou"
  if [ -n "${GITLEAKS_BIN:-}" ]; then
    echo; echo "== $GITLEAKS_BIN git 7844d7c..HEAD"; "$GITLEAKS_BIN" version
    (cd "$ROOT" && "$GITLEAKS_BIN" git --log-opts="7844d7c..HEAD" --redact -v . 2>&1 | tail -3; exit "${PIPESTATUS[0]}") || die "gitleaks encontrou vazamento no delta"
    echo "== $GITLEAKS_BIN dir $OUT"
    ("$GITLEAKS_BIN" dir --redact "$OUT" 2>&1 | tail -1; exit "${PIPESTATUS[0]}") || die "gitleaks encontrou vazamento nas provas"
  else echo "GITLEAKS_BIN não definido: gitleaks não executado"; fi
  echo; echo "== grep de senha nas provas e logs (valor real em runtime; nunca impresso)"
  SECRETS=""
  for v in OTERU_BUDGET_DEFINER_PASSWORD CLICKHOUSE_PASSWORD; do [ -n "${!v:-}" ] && SECRETS="$SECRETS ${!v}"; done
  [ -n "$SECRETS" ] || echo "(nenhuma senha definida no ambiente)"
  for sec in $SECRETS; do
    echo "comando: grep -rFc -- \"\$<senha>\" $OUT $ROOT/oteru-dbt/logs"
    n="$( { grep -rlF -- "$sec" "$OUT" "$ROOT/oteru-dbt/logs" 2>/dev/null || true; } | wc -l)"; echo "arquivos com a senha: $n"
    check "senha ausente em provas/logs" '[ "$n" = "0" ]'
  done
  echo "comando: grep -rniE --exclude='pii-gitleaks-*' 'IDENTIFIED WITH sha256_password BY|X-ClickHouse-Key: [^\$]' $OUT"
  n2="$( { grep -rniE --exclude='pii-gitleaks-*' "IDENTIFIED WITH sha256_password BY|X-ClickHouse-Key: [^\$]" "$OUT" || true; } | wc -l)"; echo "ocorrências: $n2"
  check "nenhum IDENTIFIED BY/chave nas provas" '[ "$n2" = "0" ]'
} > "$OUT/pii-gitleaks-$SHA.txt"
section_ok pii-gitleaks "$OUT/pii-gitleaks-$SHA.txt"
echo "provas em $OUT (todas as verificações PASS)"
