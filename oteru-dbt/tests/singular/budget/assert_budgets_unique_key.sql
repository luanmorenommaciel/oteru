-- #48 CA1: unicidade (scope, team, project, valid_from).
SELECT scope, team, project, valid_from, count() AS n
FROM {{ ref('budgets') }}
GROUP BY scope, team, project, valid_from
HAVING n > 1
