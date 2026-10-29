# Seed `budgets` (#48)

Caps de custo de IA, lidos por `stg_budgets` → `fct_budget_consumption_daily` →
`budget_status_current` (alerta da Vigia). Nesta entrega só há alerta: nada é bloqueado.

## Colunas

| coluna | tipo | regra |
|---|---|---|
| `scope` | LowCardinality(String) | `global`, `team`, `project` ou `team_project` |
| `team` | String | slug `^[a-z0-9][a-z0-9-]{0,62}$`; `''` quando o scope não usa team |
| `project` | String | slug; `''` quando o scope não usa project |
| `period` | LowCardinality(String) | só `month` (mês UTC) |
| `budget_usd_micros` | Int64 | `>= 0`; `0` = `no_budget` |
| `warn_milli` | UInt16 | padrão `800` (80%); `0 < warn_milli < breach_milli` |
| `breach_milli` | UInt16 | padrão `1000` (100%) |
| `action_on_breach` | LowCardinality(String) | só `alert`; `block` é rejeitado |
| `valid_from` | Date | início da vigência, inclusive |
| `valid_to` | Nullable(Date) | fim da vigência, inclusive; vazio = aberto |
| `owner_team` | String | time dono do budget, nunca uma pessoa |

Sem colunas Float, sem `@`, sem `actor_id`.

## Scopes (spec §3.1)

| scope | team | project | scope_key | soma de custo |
|---|---|---|---|---|
| global | `''` | `''` | `global` | total do dia, **incluindo** `unassigned` |
| team | slug | `''` | team | por team |
| project | `''` | slug | project | por project, todos os times |
| team_project | slug | slug | `team/project` | por team × project |

Um mesmo custo conta em todos os caps cujo grão o contém (sem rateio). Cap por time é
`scope='team'`; não há curinga.

## Vigência e override

A vigência não pode se sobrepor por (scope, team, project), e há no máximo uma linha
`global` vigente por dia. Para mudar um cap, feche a linha atual com `valid_to` e abra
outra com `valid_from` no dia seguinte, por PR. O acumulado é por mês UTC e zera na virada.

## Status

`no_budget` se `budget_usd_micros = 0`; senão `breach` se `consumido × 1000 >= budget ×
breach_milli`, `warn` se `>= budget × warn_milli`, senão `ok` (só inteiros, Int128).

As linhas atuais são exemplos; os valores reais saem por PR.

## Leitura pelo alerta e definer da view

- `budget_status_current` mostra 1 linha por scope × scope_key, cada uma no **último dia
  processado daquele scope_key** (`as_of_day`). Um scope_key sem linha no dia mais recente
  continua visível com o próprio dia. Um budget expirado continua visível com o último dia
  em que esteve vigente até a virada do mês UTC do último dia processado; depois sai.
  `as_of_ts` é o high-water mark global de custo; `is_stale` não muda `budget_status`
  (fail-open).
- A view é `SQL SECURITY DEFINER` com `DEFINER = oteru_budget_view_definer` (var
  `budget_view_definer`). Esse usuário é criado **fora do dbt**, pelo script de bootstrap
  do Alicerce, que roda no CI antes do build. Nenhuma senha ou credencial dele fica no
  repositório. Sem esse usuário o build da view falha (não há fallback).
- `audit_budget_runs` é gravado no fim do run (on-run-end) com `run_status` = `success`
  ou `error` (status final do run inteiro). Alertas devem considerar só `run_status = 'success'`.
