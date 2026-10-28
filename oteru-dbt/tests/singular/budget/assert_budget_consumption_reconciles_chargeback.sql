-- #48 CA2: para cada scope, consumed_usd_micros do dia = fct_cost_chargeback_daily
-- agregada no grão do scope (diferença 0). Só pares com budget vigente estão na fct;
-- o lado chargeback é restrito aos (dia, scope, scope_key) presentes na fct.
-- Também falha se um dia com custo não tiver linha para um budget vigente.
WITH cb AS (
  SELECT day_utc, team, project, sum(cost_usd_micros) AS m
  FROM {{ ref('fct_cost_chargeback_daily') }} GROUP BY day_utc, team, project
),
cb_grain AS (
  SELECT day_utc, 'global' AS scope, 'global' AS scope_key, sum(m) AS m FROM cb GROUP BY day_utc
  UNION ALL SELECT day_utc, 'team', team, sum(m) FROM cb GROUP BY day_utc, team
  UNION ALL SELECT day_utc, 'project', project, sum(m) FROM cb GROUP BY day_utc, project
  UNION ALL SELECT day_utc, 'team_project', concat(team, '/', project), sum(m) FROM cb GROUP BY day_utc, team, project
),
expected AS (
  -- (dia com custo) × budget vigente
  SELECT d.day_utc AS day_utc, toString(b.scope) AS scope, b.scope_key AS scope_key
  FROM (SELECT DISTINCT day_utc FROM cb) AS d
  CROSS JOIN {{ ref('stg_budgets') }} AS b
  WHERE b.valid_from <= d.day_utc AND d.day_utc <= b.valid_to_effective
),
f AS (
  SELECT day_utc, toString(scope) AS scope, scope_key, consumed_usd_micros, toUInt8(1) AS f_present
  FROM {{ ref('fct_budget_consumption_daily') }}
)
SELECT e.day_utc, e.scope, e.scope_key,
       toInt64(f.consumed_usd_micros) AS fct_micros, toInt64(g.m) AS chargeback_micros,
       if(f.f_present = 0, 'linha ausente na fct', 'diferença de micros') AS issue
FROM expected AS e
LEFT JOIN f ON e.day_utc = f.day_utc AND e.scope = f.scope AND e.scope_key = f.scope_key
LEFT JOIN cb_grain AS g ON e.day_utc = g.day_utc AND e.scope = g.scope AND e.scope_key = g.scope_key
WHERE f.f_present = 0 OR toInt64(f.consumed_usd_micros) != toInt64(g.m)
