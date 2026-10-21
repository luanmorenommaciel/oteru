-- #48 alerta Vigia A1: (scope, scope_key) cujo budget_status ATUAL em budget_status_current
-- é warn ou breach. Lê só colunas permitidas a oteru_budget_alerts_reader (§7.12).
-- Valores (budget_status_expr): ok | warn | breach | no_budget; ok e no_budget não disparam.
-- Escopos: global, team, project, team_project (sem filtro de scope).
-- Desenho (stateless + dedup): a view não tem histórico, então a query retorna uma linha por
-- avaliação ENQUANTO o par está em warn/breach, com dedup_key =
-- scope|scope_key|budget_status|toStartOfMonth(as_of_ts, UTC). O alertador (Grafana/Alertmanager)
-- notifica na 1ª ocorrência da chave e RESOLVE a chave quando a linha some. Assim:
-- warn repetido = 1 notificação; warn→breach = chave nova; warn→ok→warn no mesmo período =
-- resolve e notifica de novo; virada de mês = chave nova. Requisito: alerta com resolve
-- automático habilitado (sem isso warn→ok→warn seria suprimido pela mesma chave).
-- Período = mês de as_of_ts (period_start não é coluna permitida; budgets são mensais).
SELECT
  scope,
  scope_key,
  budget_status,
  concat(scope, '|', scope_key, '|', budget_status, '|',
         toString(toStartOfMonth(as_of_ts, 'UTC'))) AS dedup_key
FROM otel.budget_status_current
WHERE budget_status IN ('warn', 'breach')
ORDER BY scope, scope_key
