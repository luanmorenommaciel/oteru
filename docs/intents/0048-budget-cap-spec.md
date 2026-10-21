# SPEC — #48 Budget cap e circuit breaker (dados/dbt) — RASCUNHO

Status: rascunho (Modelo). Base: origin/feat/46-cost-chargeback @ e1b4f54.

## 1. Objetivo
Entregar, em dbt/ClickHouse, (a) budgets por team × project × período, (b) consumo diário e acumulado no período vs. budget, (c) status por threshold e (d) sinal de runaway, num contrato enforced que o enforcement (Alicerce) lê.

## 2. Critérios de aceite
- CA1: seed `budgets` validado (unicidade, valores positivos, vigência sem sobreposição).
- CA2: `fct_budget_consumption_daily` tem 1 linha por day_utc × team × project com budget vigente; `consumed_usd_micros` do dia = soma de fct_cost_chargeback_daily do mesmo dia/team/project (reconciliação exata, teste).
- CA3: acumulado do período (mês UTC, por padrão) é monotônico não-decrescente dentro do período e zera na virada.
- CA4: `budget_status` ∈ {ok, warn, breach, no_budget}; thresholds vêm do seed (default warn=0.8, breach=1.0) e o teste prova os limites (79,9% ok, 80% warn, 100% breach).
- CA5: nenhum modelo de budget contém actor_id nem coluna derivada de ator (teste de contrato + grep no manifest).
- CA6: `budget_status_current` dá exatamente 1 linha por team × project com o último dia processado e `as_of_ts`, pronto para o Alicerce.
- CA7: runs incrementais com lookback 48h são idempotentes (rodar 2× não muda resultado).
- CA8: nenhum contrato de #46 muda (non-breaking).

## 3. Modelos propostos
| Modelo | Tipo | Grão | Notas |
|---|---|---|---|
| `seeds/budgets.csv` | seed (contrato via yml) | team × project × valid_from | fonte inicial; pode virar source depois |
| `stg_budgets` | view | idem | tipagem, normaliza vigência (valid_to nulo = aberto) |
| `fct_budget_consumption_daily` | incremental (ReplacingMergeTree ou delete+insert por partição de dia), lookback 48h + recomputo do período corrente | day_utc × team × project | permanente, sem actor_id |
| `budget_status_current` | view (ou table pequena) | team × project | o que o enforcement lê |
| `fct_budget_consumption_hourly` (fase 2, requisito) | incremental | hour_utc × team × project | latência ≤ 1h; agrega int_cost_attributed antes de materializar (sem actor_id) |
| `audit_budget_runs` | incremental (append por run) | run_id × team × project × status | pedido do Vigia; só agregados; **não altera** `audit_cost_runs` (contrato #45/#47) |

Observação: o acumulado do período depende de dias anteriores; com lookback 48h, recalcular o acumulado como window sobre o período corrente inteiro (no máx. 31 dias por team/project — barato) em vez de somar incrementalmente.

### 3.1 Seed `budgets`
Colunas: team String, project String ('*' = todo o time? — em aberto), period LowCardinality(String) ('month'), budget_usd_micros Int64, warn_ratio Float64 (default 0.8), breach_ratio Float64 (default 1.0), action_on_breach LowCardinality(String) ('alert' → Vigia, 'block' → Alicerce; política muda por PR), valid_from Date, valid_to Nullable(Date), owner_team String.
Override: linha com vigência própria (valid_from/valid_to) no seed; também expira na virada do mês. Reversão de bloqueio = override ou virada do mês.

## 4. Contratos de colunas (enforced)
### fct_budget_consumption_daily
| coluna | tipo | regra |
|---|---|---|
| day_utc | Date | not_null |
| period_start | Date | not_null; = toStartOfMonth(day_utc) |
| team | String | not_null |
| project | String | not_null |
| owner_team | String | not_null |
| budget_usd_micros | Int64 | not_null, > 0 (linhas no_budget: 0) |
| consumed_usd_micros | Int64 | not_null, >= 0 (dia) |
| consumed_period_usd_micros | Int64 | not_null, acumulado no período |
| consumed_ratio | Float64 | consumed_period / budget (0 se no_budget) |
| warn_ratio | Float64 | |
| breach_ratio | Float64 | |
| budget_status | LowCardinality(String) | accepted_values ok/warn/breach/no_budget |
| projected_period_usd_micros | Int64 | projeção linear até o fim do período (informativa) |
| loaded_at | DateTime64(3) | |
Unicidade: (day_utc, team, project).

### budget_status_current
team, project, owner_team, period_start, as_of_day, as_of_ts DateTime64(3), budget_usd_micros, consumed_period_usd_micros, consumed_ratio, budget_status, action_on_breach, stale_threshold_hours UInt16, is_stale Bool. Unicidade: (team, project).
- Lido pelo enforcement **somente** por um role somente-leitura dedicado (grant criado pelo Alicerce).
- `is_stale = now64() - as_of_ts > toIntervalHour(stale_threshold_hours)`; o limite vem das vars dbt `budget_stale_hours_daily` (default 26) e `budget_stale_hours_hourly` (default 2, fase 2).
- Como `is_stale` depende de `now()`, o modelo é **view** (não table), para o valor ser avaliado na leitura.

### audit_budget_runs
| coluna | tipo | regra |
|---|---|---|
| run_id | String | not_null (invocation_id do dbt) |
| run_completed_at | DateTime64(3) | not_null |
| team | String | not_null |
| project | String | not_null |
| status | LowCardinality(String) | accepted_values ok/warn/breach/no_budget |
| count | UInt64 | not_null; nº de dias no período com esse status |
Unicidade: (run_id, team, project, status). Sem actor_id. `audit_cost_runs`/`audit_cost_runs_chargeback` não mudam.

## 5. Testes dbt planejados
- Genéricos: not_null, accepted_values (budget_status, period, action_on_breach), unique combinado (dbt_utils.unique_combination_of_columns ou teste singular).
- Singulares:
  - `assert_budget_consumption_reconciles_chargeback`: soma diária = fct_cost_chargeback_daily por dia × team × project (diferença 0 micros).
  - `assert_budget_period_cumulative_monotonic`.
  - `assert_budgets_no_overlapping_validity`.
  - `assert_budget_status_thresholds` (fixtures/unit test dbt 1.8+ `unit_tests:` com casos 0.799/0.8/1.0/no_budget).
  - `assert_no_actor_columns_in_budget_models` (consulta system.columns por nome ILIKE '%actor%'), incluindo `audit_budget_runs`.
  - `assert_budget_status_current_is_stale` (unit test com as_of_ts antigo/recente vs. var).
  - `assert_audit_budget_runs_matches_status` (contagens = group by do status no run).
- Unit tests dbt (`unit_tests:`) para a lógica de status e para a virada de mês.

## 6. Prova esperada
- `dbt build -s +budget_status_current` verde no ClickHouse local (sem --full-refresh), log anexado.
- Saída das queries de reconciliação (0 linhas de diferença) e de status para um fixture com 1 time em breach.
- Segunda execução incremental idêntica (hash/contagem por partição igual).
- `dbt ls --resource-type model -s budget*` + trecho do manifest mostrando contracts enforced e ausência de actor_id.

## 7. Enforcement (Alicerce)
_A preencher pelo Alicerce em commit próprio._

## 8. Riscos
- **Granularidade**: o diário (MVP) detecta um agente em loop só depois; o horário (fase 2, ≤ 1h) é o que de fato atende "runaway".
- int_cost_attributed é view com actor_id; modelos horários devem agregar antes de materializar para não vazar ator.
- Lookback 48h: custo que chegar com > 48h de atraso não entra no consumo (subestima). Precisa de monitor de atraso.
- Mudança retroativa de atribuição (service_map/actor_map) move custo entre times dentro do período e pode trocar status sem novo gasto.
- Seed como fonte de budgets: mudança exige PR/deploy; ok para início, pode não servir a donos de budget não-técnicos.
- Float em ratio: comparar em micros inteiros (consumed*1000 >= budget*warn_milli) para evitar borda de ponto flutuante.

## 9. Decisões de política pendentes (Vinícius)
1. O breaker bloqueia ou só alerta?
2. Fail-open ou fail-closed quando `is_stale = true`?
3. O `unassigned` entra em algum cap?
4. Valor dos budgets (o seed sai com valores placeholder até a decisão).

## 9.1 Questões em aberto
- Período: mês UTC calendário ou configurável (semana/dia)? Fuso UTC para o corte?
- Budget por projeto, por time (project='*') ou ambos (hierárquico)?
- Relação com #23 (não lida em detalhe aqui).
- Moeda: só USD?
- Dono dos budgets (FinOps? cada owner_team?).
- Sinal horário: além do budget, incluir pico (ex.: última hora > N× mediana de 7d)?

## 9.2 Já decidido
- Scheduler: o mesmo da #103.
- Corte por team × project; corte por ator fora do escopo.
- `alert` → Vigia; `block` → Alicerce; mudança de política por PR.

## 10. Breaking changes
Nenhuma prevista: só modelos/seed novos; contratos de #45/#46/#47 (incl. `audit_cost_runs`) inalterados.
