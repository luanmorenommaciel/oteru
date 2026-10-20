ATTACH VIEW _ UUID 'de518131-84cb-4afc-a514-496141fe115c'
(
    `day_utc` Date,
    `daily_cost_usd` Nullable(Float64),
    `daily_requests` UInt64,
    `cost_7d_avg` Nullable(Float64),
    `cost_7d_ago` Nullable(Float64),
    `cost_wow_change_pct` Nullable(Float64)
)
AS WITH
    daily_totals AS
    (
        SELECT
            day_utc,
            sum(cost_usd) AS daily_cost_usd,
            sum(requests) AS daily_requests
        FROM otel.fct_cost_daily
        GROUP BY day_utc
    ),
    with_moving_avg AS
    (
        SELECT
            day_utc,
            daily_cost_usd,
            daily_requests,
            avg(daily_cost_usd) OVER (ORDER BY day_utc ASC ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS cost_7d_avg,
            lagInFrame(daily_cost_usd, 7) OVER (ORDER BY day_utc ASC) AS cost_7d_ago
        FROM
        daily_totals
    )
SELECT
    day_utc,
    daily_cost_usd,
    daily_requests,
    cost_7d_avg,
    cost_7d_ago,
    multiIf(cost_7d_ago > 0, ((daily_cost_usd - cost_7d_ago) / cost_7d_ago) * 100, NULL) AS cost_wow_change_pct
FROM
with_moving_avg
ORDER BY day_utc ASC
