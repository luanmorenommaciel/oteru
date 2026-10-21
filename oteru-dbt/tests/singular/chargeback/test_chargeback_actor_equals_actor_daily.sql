-- #46: reconciliação EXATA em micros (e requests) por dia dentro do TTL:
-- fct_cost_chargeback_actor_daily vs fct_cost_actor_daily. FULL OUTER JOIN.

SELECT
  toDate('1970-01-01') AS day_utc,
  'fct_cost_actor_daily vazio no TTL (nunca comparar vazio com vazio)' AS issue,
  toInt64(0) AS chargeback_micros,
  toInt64(0) AS actor_micros
WHERE (SELECT count() FROM {{ ref('fct_cost_actor_daily') }}
       WHERE day_utc >= today() - {{ var('actor_cost_ttl_days') }}) = 0

UNION ALL

SELECT
  if(c.c_present = 1, c.day_utc, a.day_utc) AS day_utc,
  multiIf(c.c_present = 0, 'dia ausente em fct_cost_chargeback_actor_daily',
          a.a_present = 0, 'dia ausente em fct_cost_actor_daily',
          'divergência de micros/requests') AS issue,
  toInt64(c.micros) AS chargeback_micros,
  toInt64(a.micros) AS actor_micros
FROM (
  SELECT day_utc, sum(cost_usd_micros) AS micros, sum(requests) AS reqs, toUInt8(1) AS c_present
  FROM {{ ref('fct_cost_chargeback_actor_daily') }}
  WHERE day_utc >= today() - {{ var('actor_cost_ttl_days') }}
  GROUP BY day_utc
) AS c
FULL OUTER JOIN (
  SELECT day_utc, sum(ifNull(cost_usd_micros, 0)) AS micros, sum(requests) AS reqs, toUInt8(1) AS a_present
  FROM {{ ref('fct_cost_actor_daily') }}
  WHERE day_utc >= today() - {{ var('actor_cost_ttl_days') }}
  GROUP BY day_utc
) AS a
  ON c.day_utc = a.day_utc
WHERE c.c_present = 0 OR a.a_present = 0 OR c.micros != a.micros OR c.reqs != a.reqs
