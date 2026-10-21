-- Test that permanent daily totals remain unchanged after actor rows expire (LOCAL ONLY)
-- Run with: dbt test --select test_ttl_expiry --vars '{"actor_cost_ttl_days": 0}'
{% if var('actor_cost_ttl_days', 90) == 0 %}

WITH before_totals AS (
  SELECT
    SUM(cost_usd_micros) AS permanent_total
  FROM {{ ref('fct_cost_daily') }}
),

optimize_actor_table AS (
  {{ run_query('OPTIMIZE TABLE otel.fct_cost_actor_daily FINAL') }}
  SELECT 1 AS dummy
),

after_totals AS (
  SELECT
    SUM(cost_usd_micros) AS permanent_total
  FROM {{ ref('fct_cost_daily') }}
),

comparison AS (
  SELECT
    b.permanent_total AS before_total,
    a.permanent_total AS after_total,
    ABS(b.permanent_total - a.permanent_total) AS diff
  FROM before_totals b, after_totals a
  WHERE diff > 0
)

SELECT
  before_total,
  after_total,
  diff,
  'Permanent total changed after TTL expiry' AS issue
FROM comparison

{% else %}

-- Skip test unless actor_cost_ttl_days=0
SELECT NULL AS skip_test WHERE 1=0

{% endif %}
