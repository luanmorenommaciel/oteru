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
