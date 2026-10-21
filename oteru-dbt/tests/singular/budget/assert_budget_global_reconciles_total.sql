-- #48 §9 item 3: consumed_usd_micros do scope global = sum(cost_usd_micros) de
-- fct_cost_chargeback_daily no dia, INCLUINDO 'unassigned'; diferença exatamente 0.
-- FULL OUTER JOIN por dia (só dias com budget global vigente). Nunca vazio-contra-vazio.
WITH g AS (
  SELECT valid_from, valid_to_effective FROM {{ ref('stg_budgets') }} WHERE scope = 'global'
),
cb AS (
  SELECT day_utc, sum(cost_usd_micros) AS m, toUInt8(1) AS c_present
  FROM {{ ref('fct_cost_chargeback_daily') }}
  WHERE day_utc IN (SELECT day_utc FROM {{ ref('fct_cost_chargeback_daily') }} AS x CROSS JOIN g WHERE g.valid_from <= x.day_utc AND x.day_utc <= g.valid_to_effective)
  GROUP BY day_utc
),
f AS (
  SELECT day_utc, sum(consumed_usd_micros) AS m, count() AS n, toUInt8(1) AS f_present
  FROM {{ ref('fct_budget_consumption_daily') }} WHERE scope = 'global' GROUP BY day_utc
)
SELECT toDate('1970-01-01') AS day_utc, 'sem dia global para comparar' AS issue, toInt64(0) AS fct_micros, toInt64(0) AS chargeback_micros
WHERE (SELECT count() FROM g) > 0 AND (SELECT count() FROM cb) = 0
UNION ALL
SELECT if(c.c_present = 1, c.day_utc, f.day_utc),
       multiIf(c.c_present = 0, 'dia global sem custo no chargeback', f.f_present = 0, 'dia sem linha global', f.n != 1, 'mais de uma linha global', 'diferença de micros'),
       toInt64(f.m), toInt64(c.m)
FROM cb AS c
FULL OUTER JOIN f ON c.day_utc = f.day_utc
WHERE c.c_present = 0 OR f.f_present = 0 OR f.n != 1 OR toInt64(c.m) != toInt64(f.m)
