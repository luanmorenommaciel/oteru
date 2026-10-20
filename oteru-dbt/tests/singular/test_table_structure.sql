-- Test that fct_cost_daily has NO actor_id column and fct_cost_actor_daily HAS TTL
WITH daily_columns AS (
  SELECT
    name,
    table
  FROM system.columns
  WHERE database = 'otel' 
    AND table = 'fct_cost_daily'
    AND name = 'actor_id'
),

actor_table_ttl AS (
  SELECT
    name,
    engine_full
  FROM system.tables
  WHERE database = 'otel'
    AND name = 'fct_cost_actor_daily'
)

SELECT
  'fct_cost_daily has actor_id column (should not exist)' AS issue,
  d.name AS column_name
FROM daily_columns d

UNION ALL

SELECT
  'fct_cost_actor_daily missing TTL (should contain TTL)' AS issue,
  a.name AS table_name
FROM actor_table_ttl a
WHERE engine_full NOT LIKE '%TTL%'
