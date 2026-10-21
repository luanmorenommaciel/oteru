-- #46 alerta Vigia: linhas do actor_map com versão de hmac diferente da atual (hmac:v1:).
-- Dispara (retorna 1 linha) quando o último run com sucesso reporta > 0. Só leitura.
WITH last_run AS (
  SELECT run_id, run_completed_at
  FROM otel.audit_cost_runs
  WHERE run_status = 'success'
  ORDER BY run_completed_at DESC
  LIMIT 1
)
SELECT
  r.run_id,
  r.run_completed_at,
  max(c.metric_value) AS mismatch_rows
FROM otel.audit_cost_runs_chargeback AS c
INNER JOIN last_run AS r ON c.run_id = r.run_id
WHERE c.metric_name = 'actor_map_hmac_version_mismatch_rows'
GROUP BY r.run_id, r.run_completed_at
HAVING mismatch_rows > 0
