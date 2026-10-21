-- #46: todo run grava as métricas em audit_cost_runs_chargeback
-- (actor_map_hmac_version_mismatch_rows + unassigned_share_by_source por dia).
SELECT 'métrica ausente neste run' AS issue, m AS metric_name
FROM (SELECT arrayJoin(['actor_map_hmac_version_mismatch_rows', 'unassigned_share_by_source']) AS m)
WHERE m NOT IN (
  SELECT toString(metric_name) FROM {{ ref('audit_cost_runs_chargeback') }}
  WHERE run_id = '{{ invocation_id }}'
)
