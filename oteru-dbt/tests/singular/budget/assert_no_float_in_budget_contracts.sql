-- depends_on: {{ ref('budgets') }}
-- depends_on: {{ ref('stg_budgets') }}
-- depends_on: {{ ref('fct_budget_consumption_daily') }}
-- depends_on: {{ ref('budget_status_current') }}
-- depends_on: {{ ref('audit_budget_runs') }}
-- #48 S2 / CA9: nenhum tipo Float nos objetos de budget (seed incluído).
SELECT table, name, type
FROM system.columns
WHERE database = '{{ target.schema }}'
  AND table IN ('budgets', 'stg_budgets', 'fct_budget_consumption_daily', 'budget_status_current', 'audit_budget_runs')
  AND type ILIKE '%float%'
UNION ALL
SELECT 'objetos de budget ausentes', '', toString(n)
FROM (SELECT count() AS n FROM system.tables
WHERE database = '{{ target.schema }}'
  AND name IN ('budgets', 'stg_budgets', 'fct_budget_consumption_daily', 'budget_status_current', 'audit_budget_runs'))
WHERE n != 5
