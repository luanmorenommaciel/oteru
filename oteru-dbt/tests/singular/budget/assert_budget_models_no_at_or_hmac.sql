-- #48 CA5 / §7.9 item 7: nenhum valor com '@' ou 'hmac:' nos objetos de budget.
SELECT 'fct_budget_consumption_daily' AS model, scope_key AS value
FROM {{ ref('fct_budget_consumption_daily') }}
WHERE position(concat(scope_key, team, project, owner_team), '@') > 0 OR position(concat(scope_key, team, project, owner_team), 'hmac:') > 0
UNION ALL
SELECT 'audit_budget_runs', scope_key
FROM {{ ref('audit_budget_runs') }}
WHERE position(concat(run_id, scope_key, team, project), '@') > 0 OR position(concat(scope_key, team, project), 'hmac:') > 0
