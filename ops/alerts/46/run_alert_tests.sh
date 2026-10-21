#!/usr/bin/env bash
# Testes dos alertas Vigia #46 com clickhouse-local. Schema copiado de
# log_run_audit.sql (audit_cost_runs) e chargeback.yml (audit_cost_runs_chargeback).
set -euo pipefail
cd "$(dirname "$0")"
CH=${CH:-./clickhouse}
DDL="CREATE DATABASE otel;
CREATE TABLE otel.audit_cost_runs (run_id String, run_started_at String, run_completed_at DateTime64(9), target_name String, dbt_command String, duration_seconds Int64, rows_processed Int64, max_event_ts_read DateTime64(9), events_dropped_late Int64, rows_empty_request_id Int64, run_status String) ENGINE=MergeTree ORDER BY (run_started_at, run_id);
CREATE TABLE otel.audit_cost_runs_chargeback (run_id String, metric_name LowCardinality(String), day_utc Date, attribution_source LowCardinality(String), metric_value Float64) ENGINE=MergeTree ORDER BY (run_id, metric_name, day_utc, attribution_source);"
run() { # $1 nome, $2 fixture sql, $3 query, $4 linhas esperadas
  local out n
  out=$($CH local --multiquery --query "$DDL $2; $(cat "$3")" </dev/null)
  n=$(printf '%s' "$out" | grep -c . || true)
  if [[ "$n" == "$4" ]]; then echo "PASS $1 (linhas=$n) $out"; else echo "FAIL $1 esperado=$4 obtido=$n: $out"; exit 1; fi
}
runrow() { echo "INSERT INTO otel.audit_cost_runs VALUES ('$1','x',now64(9,'UTC') - $2,'dev','build',3,10,now64(9,'UTC'),0,0,'$3')"; }
# série de 8 dias: unassigned share = $1 nos 7 anteriores, $2 no último fechado
share() { local s=""; for i in 1 2 3 4 5 6 7; do s+="INSERT INTO otel.audit_cost_runs_chargeback VALUES ('$3','unassigned_share_by_source',toDate(now('UTC'))-1-$i,'unassigned',$1),('$3','unassigned_share_by_source',toDate(now('UTC'))-1-$i,'service_map',1-$1);"; done
  s+="INSERT INTO otel.audit_cost_runs_chargeback VALUES ('$3','unassigned_share_by_source',toDate(now('UTC'))-1,'unassigned',$2),('$3','unassigned_share_by_source',toDate(now('UTC'))-1,'service_map',1-$2)"; echo "$s"; }
mm() { echo "INSERT INTO otel.audit_cost_runs_chargeback VALUES ('$1','actor_map_hmac_version_mismatch_rows',toDate(now('UTC')),'actor_map',$2)"; }
H=chargeback_hmac_version_mismatch.sql
U=chargeback_unassigned_share_spike.sql
run hmac_ok            "$(runrow r1 0 success); $(mm r1 0)" $H 0
run hmac_dispara       "$(runrow r1 0 success); $(mm r1 2)" $H 1
run hmac_ignora_falho  "$(runrow r1 60 success); $(mm r1 0); $(runrow r2 0 error); $(mm r2 5)" $H 0
run hmac_so_ultimo     "$(runrow r1 60 success); $(mm r1 3); $(runrow r2 0 success); $(mm r2 0)" $H 0
run unassigned_ok      "$(runrow r1 0 success); $(share 0.10 0.14 r1)" $U 0
run unassigned_dispara "$(runrow r1 0 success); $(share 0.10 0.16 r1)" $U 1
run unassigned_limite  "$(runrow r1 0 success); $(share 0.10 0.15 r1)" $U 0
run unassigned_base_zero "$(runrow r1 0 success); $(share 0.0 0.06 r1)" $U 1
run unassigned_historico_curto "$(runrow r1 0 success); INSERT INTO otel.audit_cost_runs_chargeback VALUES ('r1','unassigned_share_by_source',toDate(now('UTC'))-1,'unassigned',0.9),('r1','unassigned_share_by_source',toDate(now('UTC'))-2,'unassigned',0.1)" $U 0
echo "TODOS OS TESTES PASSARAM"
