-- #46 (Sentinela, cond. 5): audit_cost_runs_chargeback não tem coluna de ator
-- nem valores HMAC.
-- depends_on: {{ ref('audit_cost_runs_chargeback') }}
SELECT 'coluna proibida' AS issue, name AS detail
FROM system.columns
WHERE database = '{{ target.schema }}' AND table = 'audit_cost_runs_chargeback'
  AND (name ILIKE '%actor%' OR name ILIKE '%hmac%' OR name ILIKE '%email%' OR name ILIKE '%user%')
UNION ALL
SELECT 'valor hmac/@ em coluna String', v
FROM (
  SELECT arrayJoin([run_id, toString(metric_name), toString(attribution_source)]) AS v
  FROM {{ ref('audit_cost_runs_chargeback') }}
)
WHERE v ILIKE '%hmac:%' OR position(v, '@') > 0
UNION ALL
SELECT 'tabela ausente', 'audit_cost_runs_chargeback'
WHERE (SELECT count() FROM system.tables WHERE database = '{{ target.schema }}' AND name = 'audit_cost_runs_chargeback') = 0
