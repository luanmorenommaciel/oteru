-- #48 (Maestro): audit_budget_runs.run_completed_at é DateTime64(3, 'UTC') na tabela física.
-- depends_on: {{ ref('audit_budget_runs') }}
SELECT name, type FROM system.columns
WHERE database = '{{ target.schema }}' AND table = 'audit_budget_runs' AND name = 'run_completed_at'
  AND type != 'DateTime64(3, \'UTC\')'
UNION ALL
SELECT 'run_completed_at ausente', '' FROM (SELECT count() AS n FROM system.columns
  WHERE database = '{{ target.schema }}' AND table = 'audit_budget_runs' AND name = 'run_completed_at') WHERE n != 1
