-- #46: unicidade do grão (dbt_utils não está instalado; teste singular).
SELECT 'fct_cost_chargeback_daily' AS obj, toString(day_utc) AS day_utc, team, project, owner_team AS k4, toString(attribution_source) AS k5, count() AS n
FROM {{ ref('fct_cost_chargeback_daily') }}
GROUP BY day_utc, team, project, owner_team, attribution_source
HAVING n > 1
UNION ALL
SELECT 'fct_cost_chargeback_actor_daily', toString(day_utc), team, project, actor_id, '', count() AS n
FROM {{ ref('fct_cost_chargeback_actor_daily') }}
GROUP BY day_utc, team, project, actor_id
HAVING n > 1
