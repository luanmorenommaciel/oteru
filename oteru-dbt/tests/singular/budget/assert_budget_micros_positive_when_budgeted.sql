-- #48 S3 / CA10.
SELECT 'fct_budget_consumption_daily' AS model, scope, scope_key, budget_usd_micros, budget_status
FROM {{ ref('fct_budget_consumption_daily') }}
WHERE budget_usd_micros < 0 OR (budget_usd_micros = 0 AND budget_status != 'no_budget')
   OR (budget_usd_micros > 0 AND budget_status = 'no_budget')
UNION ALL
SELECT 'budget_status_current', scope, scope_key, budget_usd_micros, budget_status
FROM {{ ref('budget_status_current') }}
WHERE budget_usd_micros < 0 OR (budget_usd_micros = 0 AND budget_status != 'no_budget')
   OR (budget_usd_micros > 0 AND budget_status = 'no_budget')
