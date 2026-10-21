-- #48 alerta Vigia A1: (scope, scope_key) cujo budget_status ATUAL em budget_status_current
-- é warn ou breach. Lê só colunas permitidas a oteru_budget_alerts_reader (§7.12 + period_start).
-- Valores (budget_status_expr): ok | warn | breach | no_budget; ok e no_budget não disparam.
-- Escopos: global, team, project, team_project (sem filtro de scope).
-- Desenho (stateless + dedup): a view não tem histórico, então a query retorna uma linha por
-- avaliação ENQUANTO o par está em warn/breach, com dedup_key =
-- scope|scope_key|budget_status|period_start. O alertador (Grafana/Alertmanager)
-- notifica na 1ª ocorrência da chave e RESOLVE a chave quando a linha some. Assim:
-- warn repetido = 1 notificação; warn→breach = chave nova; warn→ok→warn no mesmo período =
-- resolve e notifica de novo; novo período (period_start) = chave nova. Requisito: alerta com resolve
-- automático habilitado (sem isso warn→ok→warn seria suprimido pela mesma chave).
-- Só o período corrente dispara: period_start = max(period_start) da própria view. Um
-- scope_key cujo último dia ficou no mês anterior (estado carregado) não dispara.
-- Período = period_start da view (coluna concedida a oteru_budget_alerts_reader pelo grant do Alicerce).
SELECT
  scope,
  scope_key,
  budget_status,
  concat(scope, '|', scope_key, '|', budget_status, '|',
         toString(period_start)) AS dedup_key
FROM otel.budget_status_current
WHERE budget_status IN ('warn', 'breach')
  AND period_start = (SELECT max(period_start) FROM otel.budget_status_current)
ORDER BY scope, scope_key
