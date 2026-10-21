-- #48 CA6: exatamente 1 linha por (scope, scope_key) em budget_status_current; cada
-- linha está no último dia do PRÓPRIO scope_key; todo scope_key com linha no período do
-- último dia processado aparece na view (não some por não ter linha no dia mais recente).
WITH f AS (SELECT * FROM {{ ref('fct_budget_consumption_daily') }}),
k AS (SELECT scope, scope_key, max(day_utc) AS d FROM f GROUP BY scope, scope_key),
g AS (SELECT max(day_utc) AS d FROM f)
SELECT toString(scope) AS scope, scope_key, toString(count()) AS detail, 'duplicada' AS issue
FROM {{ ref('budget_status_current') }} GROUP BY scope, scope_key HAVING count() > 1
UNION ALL
SELECT toString(v.scope), v.scope_key, toString(v.as_of_day), 'as_of_day != último dia do scope_key'
FROM {{ ref('budget_status_current') }} AS v
INNER JOIN k ON v.scope = k.scope AND v.scope_key = k.scope_key
WHERE v.as_of_day != k.d
UNION ALL
SELECT toString(k.scope), k.scope_key, toString(k.d), 'ausente na view'
FROM k
LEFT ANTI JOIN {{ ref('budget_status_current') }} AS v ON k.scope = v.scope AND k.scope_key = v.scope_key
WHERE toStartOfMonth(k.d) = toStartOfMonth((SELECT d FROM g))
