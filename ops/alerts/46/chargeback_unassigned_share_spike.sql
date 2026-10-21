-- #46 alerta Vigia: fatia 'unassigned' do último dia UTC fechado sobe > 5 p.p.
-- sobre a mediana dos 7 dias anteriores. Fonte: último run com sucesso
-- (unassigned_share_by_source cobre os últimos 8 dias). Dia sem linha unassigned conta 0.
-- Dispara (retorna 1 linha) só com 7 dias de histórico. Só leitura.
WITH last_run AS (
  SELECT run_id
  FROM otel.audit_cost_runs
  WHERE run_status = 'success'
  ORDER BY run_completed_at DESC
  LIMIT 1
),
days AS (
  SELECT
    c.day_utc,
    sumIf(c.metric_value, c.attribution_source = 'unassigned') AS share
  FROM otel.audit_cost_runs_chargeback AS c
  INNER JOIN last_run AS r ON c.run_id = r.run_id
  WHERE c.metric_name = 'unassigned_share_by_source'
  GROUP BY c.day_utc
),
target AS (
  SELECT day_utc, share FROM days
  WHERE day_utc = (SELECT max(day_utc) FROM days WHERE day_utc < toDate(now('UTC')))
),
baseline AS (
  SELECT median(d.share) AS med, count() AS n
  FROM days AS d
  WHERE d.day_utc < (SELECT day_utc FROM target)
    AND d.day_utc >= (SELECT day_utc FROM target) - 7
)
SELECT
  t.day_utc,
  round(t.share, 4) AS unassigned_share,
  round(b.med, 4) AS median_7d,
  round((t.share - b.med) * 100, 2) AS delta_pp
FROM target AS t
CROSS JOIN baseline AS b
WHERE b.n = 7 AND t.share - b.med > 0.05
