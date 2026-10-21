#!/usr/bin/env bash
# Testes dos alertas Vigia #48 com clickhouse-local. Schemas = contratos de
# budget_status_current / audit_budget_runs (patch Modelo #48) e colunas permitidas de audit_cost_runs.
set -euo pipefail
cd "$(dirname "$0")"
CH=${CH:-./clickhouse}
DDL="CREATE DATABASE otel;
CREATE TABLE otel.budget_status_current (scope LowCardinality(String), scope_key String, team String, project String, owner_team String, period_start Date, as_of_day Date, as_of_ts DateTime64(9), budget_usd_micros Int64, consumed_period_usd_micros Int64, consumed_milli Int64, warn_milli UInt16, breach_milli UInt16, budget_status LowCardinality(String), action_on_breach LowCardinality(String), stale_threshold_hours UInt16, is_stale Bool) ENGINE=MergeTree ORDER BY (scope, scope_key);
CREATE TABLE otel.audit_budget_runs (run_id String, run_completed_at DateTime64(3, 'UTC'), run_status LowCardinality(String), scope LowCardinality(String), scope_key String, team String, project String, status LowCardinality(String), count UInt64) ENGINE=MergeTree ORDER BY (run_completed_at, run_id, scope, scope_key, status);
CREATE TABLE otel.audit_cost_runs (run_id String, run_completed_at DateTime64(9), run_status String, events_dropped_late Int64) ENGINE=MergeTree ORDER BY run_id;"
run() { # $1 nome, $2 fixture sql, $3 query, $4 linhas esperadas
  local out n
  out=$($CH local --multiquery --query "$DDL $2; $(cat "$3")" </dev/null)
  n=$(printf '%s' "$out" | grep -c . || true)
  if [[ "$n" == "$4" ]]; then echo "PASS $1 (linhas=$n) $out"; else echo "FAIL $1 esperado=$4 obtido=$n: $out"; exit 1; fi
}
# br run segundos_atras scope scope_key status count [run_status]
br() { echo "INSERT INTO otel.audit_budget_runs VALUES ('$1',now64(3,'UTC') - $2,'${7:-success}','$3','$4','','','$5',$6)"; }
# bs scope scope_key status is_stale [as_of_ts] [period_start]
bs() { echo "INSERT INTO otel.budget_status_current VALUES ('$1','$2','a','p','a',${6:-toDate(now('UTC'))},toDate(now('UTC')),${5:-now64(9,'UTC')},1000000,900000,900,800,1000,'$3','alert',26,$4)"; }
# cr run segundos_atras status dropped
cr() { echo "INSERT INTO otel.audit_cost_runs VALUES ('$1',now64(9,'UTC') - $2,'$3',$4)"; }
Y="toDateTime64(toDate(now('UTC')) - 1, 9, 'UTC') + 43200"   # meio-dia UTC de ontem
crd() { echo "INSERT INTO otel.audit_cost_runs VALUES ('$1',$2,'$3',$4)"; }
A1=budget_warn_breach_new.sql; A2=budget_status_stale.sql; A3=budget_run_missing.sql; A4=cost_late_beyond_lookback.sql
run a1_ok                 "$(bs global global ok false); $(bs team a ok false)" $A1 0
run a1_dispara_warn       "$(bs team a warn false); $(bs team b ok false)" $A1 1
run a1_dispara_breach     "$(bs project p breach false)" $A1 1
run a1_no_budget_ignora   "$(bs team_project a/p no_budget false); $(bs team a ok false)" $A1 0
run a1_quatro_escopos     "$(bs global global breach false); $(bs team a warn false); $(bs project p warn false); $(bs team_project a/p breach false)" $A1 4
run a1_stale_ainda_dispara "$(bs team a breach true)" $A1 1
NOW="now64(9, 'UTC')"; PANT="toStartOfMonth(toDate(now('UTC')) - 31)"; PCUR="toStartOfMonth(toDate(now('UTC')))"
run a1_periodo_anterior_nao_dispara "$(bs team a warn false "$NOW" "$PANT"); $(bs team b ok false "$NOW" "$PCUR")" $A1 0
# sequência de avaliações no mesmo período: warn (abre) -> ok (resolve) -> warn (reabre)
J="toDateTime64('2026-10-15 12:00:00', 9, 'UTC')"
run a1_seq1_warn          "$(bs team a warn false "$J")" $A1 1
run a1_seq2_ok_resolve    "$(bs team a ok false "$J")" $A1 0
run a1_seq3_warn_reabre   "$(bs team a warn false "$J")" $A1 1
dk() { $CH local --multiquery --query "$DDL $1; $(cat $A1)" </dev/null | cut -f4; }
P1="toDate('2026-10-01')"; P2="toDate('2026-11-01')"
k1=$(dk "$(bs team a warn false "$J" "$P1")"); k2=$(dk "$(bs team a warn false "toDateTime64('2026-11-02 08:00:00',9,'UTC')" "$P1")")
k3=$(dk "$(bs team a breach false "$J" "$P1")"); k4=$(dk "$(bs team a warn false "$J" "$P2")")
if [[ "$k1" == "$k2" && "$k1" != "$k3" && "$k1" != "$k4" ]]; then echo "PASS a1_dedup_key ($k1 = mesmo period_start mesmo com as_of_ts em outro mês; $k3 = status novo; $k4 = period_start novo)"; else echo "FAIL a1_dedup_key $k1 $k2 $k3 $k4"; exit 1; fi
run a2_ok                 "$(bs team a ok false); $(bs global global warn false)" $A2 0
run a2_dispara            "$(bs team a ok false); $(bs global global warn true)" $A2 1
run a3_ok                 "$(br r1 3600 team a ok 1)" $A3 0
run a3_dispara            "$(br r1 $((27*3600)) team a ok 1)" $A3 1
run a3_limite             "$(br r1 $((26*3600-60)) team a ok 1)" $A3 0
run a3_tabela_vazia       "SELECT 1 FORMAT Null" $A3 1
run a3_so_run_error      "$(br r1 3600 team a ok 1 error)" $A3 1
run a3_error_recente_success_antigo "$(br r1 $((30*3600)) team a ok 1); $(br r2 60 team a ok 1 error)" $A3 1
run a3_tipo_utc           "SELECT throwIf(toTypeName(run_completed_at) != 'DateTime64(3, \\'UTC\\')', 'tipo') FROM otel.audit_budget_runs FORMAT Null; $(br r1 3600 team a ok 1)" $A3 0
run a4_ok                 "$(crd c1 "$Y" success 0)" $A4 0
run a4_dispara            "$(crd c1 "$Y" success 3); $(crd c2 "$Y" success 0)" $A4 1
run a4_ignora_run_falho   "$(crd c1 "$Y" error 5); $(crd c2 "$Y" success 0)" $A4 0
run a4_ignora_dia_corrente "$(crd c1 "toDateTime64(toDate(now('UTC')), 9, 'UTC')" success 7); $(crd c2 "$Y" success 0)" $A4 0
echo "TODOS OS TESTES PASSARAM"
