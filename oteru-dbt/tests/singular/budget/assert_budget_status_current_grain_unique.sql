-- #48 CA6: exatamente 1 linha por (scope, scope_key) em budget_status_current, e
-- toda linha da fct no último dia processado aparece na view.
SELECT scope, scope_key, count() AS n, 'duplicada' AS issue
FROM {{ ref('budget_status_current') }} GROUP BY scope, scope_key HAVING n > 1
UNION ALL
SELECT f.scope, f.scope_key, toUInt64(0), 'ausente na view'
FROM {{ ref('fct_budget_consumption_daily') }} AS f
LEFT ANTI JOIN {{ ref('budget_status_current') }} AS v ON f.scope = v.scope AND f.scope_key = v.scope_key
WHERE f.day_utc = (SELECT max(day_utc) FROM {{ ref('fct_budget_consumption_daily') }})
