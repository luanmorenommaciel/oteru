-- Must run AFTER the fact models exist. Without these refs, dbt can schedule
-- this test before CREATE TABLE; an empty system.tables result set then
-- passes vacuously.
-- depends_on: {{ ref('fct_cost_daily') }}
-- depends_on: {{ ref('fct_cost_actor_daily') }}

-- Fail if fct_cost_daily has an actor_id column.
SELECT
  'fct_cost_daily has actor_id column (should not exist)' AS issue,
  name AS detail
FROM system.columns
WHERE database = 'otel'
  AND table = 'fct_cost_daily'
  AND name = 'actor_id'

UNION ALL

-- Fail if the actor table is missing or has no TTL in the physical DDL.
SELECT
  'fct_cost_actor_daily missing or has no TTL in create_table_query/engine_full' AS issue,
  'fct_cost_actor_daily' AS detail
WHERE NOT EXISTS (
  SELECT 1
  FROM system.tables
  WHERE database = 'otel'
    AND name = 'fct_cost_actor_daily'
    AND (
      engine_full LIKE '%TTL%'
      OR create_table_query ILIKE '%TTL%'
    )
)
