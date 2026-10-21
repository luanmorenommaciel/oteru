-- #46: reconciliação EXATA em micros (e requests) por dia:
-- fct_cost_chargeback_daily vs fct_cost_daily. FULL OUTER JOIN por dia; dia
-- ausente de um lado = falha. Flags *_present evitam o default 1970-01-01 que
-- o ClickHouse devolve no lado sem match (join_use_nulls=0).
-- Nunca passa vazio-contra-vazio: fct_cost_daily com 0 linhas é falha.

SELECT
  toDate('1970-01-01') AS day_utc,
  'fct_cost_daily vazio (nunca comparar vazio com vazio)' AS issue,
  toInt64(0) AS chargeback_micros,
  toInt64(0) AS daily_micros,
  toUInt64(0) AS chargeback_requests,
  toUInt64(0) AS daily_requests
WHERE (SELECT count() FROM {{ ref('fct_cost_daily') }}) = 0

UNION ALL

SELECT
  if(c.c_present = 1, c.day_utc, d.day_utc) AS day_utc,
  multiIf(c.c_present = 0, 'dia ausente em fct_cost_chargeback_daily',
          d.d_present = 0, 'dia ausente em fct_cost_daily',
          'divergência de micros/requests') AS issue,
  toInt64(c.micros) AS chargeback_micros,
  toInt64(d.micros) AS daily_micros,
  toUInt64(c.reqs) AS chargeback_requests,
  toUInt64(d.reqs) AS daily_requests
FROM (
  SELECT day_utc, sum(cost_usd_micros) AS micros, sum(requests) AS reqs, toUInt8(1) AS c_present
  FROM {{ ref('fct_cost_chargeback_daily') }}
  GROUP BY day_utc
) AS c
FULL OUTER JOIN (
  SELECT day_utc, sum(ifNull(cost_usd_micros, 0)) AS micros, sum(requests) AS reqs, toUInt8(1) AS d_present
  FROM {{ ref('fct_cost_daily') }}
  GROUP BY day_utc
) AS d
  ON c.day_utc = d.day_utc
WHERE c.c_present = 0 OR d.d_present = 0 OR c.micros != d.micros OR c.reqs != d.reqs
