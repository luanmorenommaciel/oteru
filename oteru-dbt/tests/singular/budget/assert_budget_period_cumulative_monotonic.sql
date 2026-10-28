-- #48 CA3: dentro do período o acumulado é não-decrescente, = soma dos dias, e
-- reinicia na virada (period_start = toStartOfMonth(day_utc)).
WITH f AS (
  SELECT day_utc, period_start, scope, scope_key, consumed_usd_micros, consumed_period_usd_micros,
         sum(consumed_usd_micros) OVER w AS running,
         lagInFrame(consumed_period_usd_micros, 1, toInt64(0)) OVER w AS prev
  FROM {{ ref('fct_budget_consumption_daily') }}
  WINDOW w AS (PARTITION BY scope, scope_key, period_start ORDER BY day_utc ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT day_utc, scope, scope_key, consumed_period_usd_micros, prev, running
FROM f
WHERE period_start != toStartOfMonth(day_utc)
   OR consumed_period_usd_micros < prev
   OR consumed_period_usd_micros != running
   OR consumed_usd_micros < 0
