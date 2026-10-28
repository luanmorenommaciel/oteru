-- #48 alerta Vigia A4: soma de events_dropped_late dos runs com sucesso de audit_cost_runs
-- concluídos no último dia UTC fechado; dispara quando > 0. Só colunas permitidas (§7.12).
SELECT 'events_dropped_late' AS metric, toDate(now('UTC')) - 1 AS day_utc,
       count() AS runs, sum(events_dropped_late) AS dropped_late
FROM otel.audit_cost_runs
WHERE run_status = 'success'
  AND toDate(run_completed_at, 'UTC') = toDate(now('UTC')) - 1
HAVING dropped_late > 0
