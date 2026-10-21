-- #46: estrutura física via system.tables/system.columns.
-- fct_cost_chargeback_daily: existe, sem TTL, sem actor_id.
-- fct_cost_chargeback_actor_daily: existe, com TTL day_utc + toIntervalDay(actor_cost_ttl_days).
-- depends_on: {{ ref('fct_cost_chargeback_daily') }}
-- depends_on: {{ ref('fct_cost_chargeback_actor_daily') }}
SELECT 'fct_cost_chargeback_daily ausente' AS issue
WHERE (SELECT count() FROM system.tables WHERE database = '{{ target.schema }}' AND name = 'fct_cost_chargeback_daily') = 0
UNION ALL
SELECT 'fct_cost_chargeback_daily tem TTL (deve ser permanente)'
FROM system.tables
WHERE database = '{{ target.schema }}' AND name = 'fct_cost_chargeback_daily'
  AND (engine_full ILIKE '%TTL%' OR create_table_query ILIKE '%TTL%')
UNION ALL
SELECT 'fct_cost_chargeback_daily tem coluna actor_id'
FROM system.columns
WHERE database = '{{ target.schema }}' AND table = 'fct_cost_chargeback_daily' AND name = 'actor_id'
UNION ALL
SELECT 'fct_cost_chargeback_actor_daily ausente ou sem TTL day_utc + toIntervalDay({{ var("actor_cost_ttl_days") }})'
WHERE (
  SELECT count() FROM system.tables
  WHERE database = '{{ target.schema }}' AND name = 'fct_cost_chargeback_actor_daily'
    AND position(engine_full, 'TTL day_utc + toIntervalDay({{ var("actor_cost_ttl_days") }})') > 0
) = 0
