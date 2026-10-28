-- #48: unicidade (day_utc, scope, scope_key) na fct.
SELECT day_utc, scope, scope_key, count() AS n
FROM {{ ref('fct_budget_consumption_daily') }}
GROUP BY day_utc, scope, scope_key HAVING n > 1
