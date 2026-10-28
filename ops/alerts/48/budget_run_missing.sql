-- #48 alerta Vigia A3: dispara (1 linha) se nenhum run com run_status = 'success' de
-- audit_budget_runs terminou nas últimas 26 h, inclusive tabela vazia ou só runs 'error'.
-- run_completed_at é DateTime64(3, 'UTC'); comparação em UTC.
SELECT 'budget_run_missing' AS metric, count() AS success_rows_last_26h
FROM otel.audit_budget_runs
WHERE run_status = 'success'
  AND run_completed_at >= now64(3, 'UTC') - INTERVAL 26 HOUR
HAVING success_rows_last_26h = 0
