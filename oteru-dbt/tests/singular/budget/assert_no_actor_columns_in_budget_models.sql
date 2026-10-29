-- depends_on: {{ ref('budgets') }}
-- depends_on: {{ ref('stg_budgets') }}
-- depends_on: {{ ref('fct_budget_consumption_daily') }}
-- depends_on: {{ ref('budget_status_current') }}
-- depends_on: {{ ref('audit_budget_runs') }}
-- #48 CA5: nenhuma coluna de ator nos objetos de budget (incl. audit_budget_runs).
SELECT table, name, type
FROM system.columns
WHERE database = '{{ target.schema }}'
  AND table IN ('budgets', 'stg_budgets', 'fct_budget_consumption_daily', 'budget_status_current', 'audit_budget_runs')
  AND (name ILIKE '%actor%' OR name ILIKE '%user%' OR name ILIKE '%email%')
