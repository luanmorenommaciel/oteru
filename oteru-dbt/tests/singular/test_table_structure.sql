-- Test that fct_cost_daily has NO actor_id column and fct_cost_actor_daily HAS TTL
WITH daily_table_check AS (
  SELECT
    database,
    name,
    has_own_data,
    arrayExists(x -> x = 'actor_id', columns.name) AS has_actor_id
  FROM system.tables
  ARRAY JOIN columns
  WHERE database = 'otel' 
    AND name = 'fct_cost_daily'
  LIMIT 1
),

actor_table_check AS (
  SELECT
    database,
    name,
    engine_full LIKE '%TTL%' AS has_ttl
  FROM system.tables
  WHERE database = 'otel'
    AND name = 'fct_cost_actor_daily'
)

SELECT
  'fct_cost_daily has actor_id column (should be false)' AS issue,
  d.has_actor_id AS value
FROM daily_table_check d
WHERE d.has_actor_id = 1

UNION ALL

SELECT
  'fct_cost_actor_daily missing TTL (should be true)' AS issue,
  a.has_ttl AS value
FROM actor_table_check a
WHERE a.has_ttl = 0
