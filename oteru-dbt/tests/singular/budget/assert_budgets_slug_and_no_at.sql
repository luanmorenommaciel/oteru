-- #48 S4: team/project preenchidos casam com o slug da #46 e nenhuma coluna tem '@'.
SELECT scope, team, project, valid_from, 'slug inválido' AS issue
FROM {{ ref('budgets') }}
WHERE (team != '' AND NOT match(team, '{{ chargeback_slug_regex() }}'))
   OR (project != '' AND NOT match(project, '{{ chargeback_slug_regex() }}'))
UNION ALL
SELECT scope, team, project, valid_from, 'contém @' AS issue
FROM {{ ref('budgets') }}
WHERE position(concat(toString(scope), team, project, toString(period), toString(budget_usd_micros),
        toString(warn_milli), toString(breach_milli), toString(action_on_breach), toString(valid_from),
        ifNull(toString(valid_to), ''), owner_team), '@') > 0
