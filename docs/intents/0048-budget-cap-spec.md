# SPEC — #48 Budget cap e circuit breaker (dados/dbt) — RASCUNHO

Status: rascunho (Modelo). Base: origin/feat/46-cost-chargeback @ e1b4f54. Condições da Sentinela (S1–S5) aplicadas nas seções de dados; §7 inalterado.

## 1. Objetivo
Entregar, em dbt/ClickHouse, (a) budgets por team × project × período, (b) consumo diário e acumulado no período vs. budget, (c) status por threshold e (d) sinal de runaway, num contrato enforced que o enforcement (Alicerce) lê.

## 2. Critérios de aceite
- CA1: seed `budgets` validado (unicidade, vigência sem sobreposição, `budget_usd_micros >= 0`, `0 < warn_milli < breach_milli`, `action_on_breach` ∈ {alert, block}, team/project no slug da #46, nenhum `@`) — S4.
- CA2: `fct_budget_consumption_daily` tem 1 linha por day_utc × team × project com budget vigente; `consumed_usd_micros` do dia = soma de fct_cost_chargeback_daily do mesmo dia/team/project (reconciliação exata, teste).
- CA3: acumulado do período (mês UTC, por padrão) é monotônico não-decrescente dentro do período e zera na virada.
- CA4: `budget_status` ∈ {ok, warn, breach, no_budget}; thresholds vêm do seed como inteiros (`warn_milli` default 800, `breach_milli` default 1000) e o status é calculado só com aritmética inteira; unit tests nas bordas exatas 79,9% ok, 80,0% warn, 100,0% breach — S2.
- CA5: nenhum modelo de budget contém actor_id nem coluna derivada de ator (teste de contrato + grep no manifest).
- CA6: `budget_status_current` dá exatamente 1 linha por team × project com o último dia processado e `as_of_ts` = último timestamp de custo efetivamente processado (não a hora do run); um run bem-sucedido sem custo novo deixa `is_stale = true` — S1.
- CA7: runs incrementais com lookback 48h são idempotentes (rodar 2× não muda resultado).
- CA8: nenhum contrato de #46 muda (non-breaking).
- CA9: nenhum `Float64` no seed nem nos contratos de budget — S2.
- CA10: `budget_usd_micros >= 0` sempre e `> 0` quando `budget_status != 'no_budget'` — S3.
- CA11: `budget_status_current` é `SQL SECURITY DEFINER` com definer de privilégio mínimo; o role leitor lê a view e recebe erro de acesso no SELECT direto em `fct_*` — S5.

## 3. Modelos propostos
| Modelo | Tipo | Grão | Notas |
|---|---|---|---|
| `seeds/budgets.csv` | seed (contrato via yml) | team × project × valid_from | fonte inicial; pode virar source depois |
| `stg_budgets` | view | idem | tipagem, normaliza vigência (valid_to nulo = aberto) |
| `fct_budget_consumption_daily` | incremental (ReplacingMergeTree ou delete+insert por partição de dia), lookback 48h + recomputo do período corrente | day_utc × team × project | permanente, sem actor_id |
| `budget_status_current` | view `SQL SECURITY DEFINER` (S5) | team × project | o que o enforcement lê; view obrigatória (`is_stale` avaliado na leitura) |
| `fct_budget_consumption_hourly` (fase 2, requisito) | incremental | hour_utc × team × project | latência ≤ 1h; agrega int_cost_attributed antes de materializar (sem actor_id) |
| `audit_budget_runs` | incremental (append por run) | run_id × team × project × status | pedido do Vigia; só agregados; **não altera** `audit_cost_runs` (contrato #45/#47) |

Observação: o acumulado do período depende de dias anteriores; com lookback 48h, recalcular o acumulado como window sobre o período corrente inteiro (no máx. 31 dias por team/project — barato) em vez de somar incrementalmente.

### 3.1 Seed `budgets`
Colunas: team String, project String ('*' = todo o time? — em aberto), period LowCardinality(String) ('month'), budget_usd_micros Int64 (>= 0), warn_milli UInt16 (default 800), breach_milli UInt16 (default 1000), action_on_breach LowCardinality(String) ('alert' → Vigia, 'block' → Alicerce; política muda por PR), valid_from Date, valid_to Nullable(Date), owner_team String.
Sem nenhuma coluna Float (S2).

**Validação do seed (S4)** — testes dbt em `seeds/budgets.yml`:
- `0 < warn_milli < breach_milli` (teste singular `assert_budgets_threshold_order`).
- `action_on_breach` em ('alert','block') (`accepted_values`).
- `team` e `project` casam com o slug da #46, `^[a-z0-9][a-z0-9-]{0,62}$`. Fonte: `CHARGEBACK_SLUG` em `oteru-collector/privacy/compile.py`, que é espelhado no OTTL `IsMatch` do mesmo arquivo. Não há macro dbt com essa regex hoje, então a proposta é criar a macro `chargeback_slug_regex()` com o mesmo literal mais um teste genérico `matches_chargeback_slug` (`match(col, regex)` no ClickHouse). O literal deve ser idêntico ao do collector, e um teste do Alicerce/CI pode comparar os dois.
- Nenhum `@` em nenhuma coluna (`position(col,'@') = 0`).
- `budget_usd_micros >= 0` (S3).
- Consequência: `project='*'` (§9.1) **não** casa com o slug; se o cap por time for aprovado, ele precisa de outra representação (ex.: coluna `scope` ∈ {team, project}).
- **Guard pré-commit/CI que cobre `seeds/budgets.csv`: fica com o Alicerce** (extensão de `scripts/check_chargeback_seeds.py` / `.github/workflows/ci.yml`). O Modelo entrega só os testes dbt acima.

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
| budget_usd_micros | Int64 | not_null, `>= 0`; `> 0` sempre que `budget_status != 'no_budget'` (S3); 0 em no_budget |
| consumed_usd_micros | Int64 | not_null, >= 0 (dia) |
| consumed_period_usd_micros | Int64 | not_null, acumulado no período |
| consumed_milli | Int64 | `intDiv(consumed_period_usd_micros * 1000, budget_usd_micros)` (0 se no_budget); informativo, truncado |
| warn_milli | UInt16 | do seed |
| breach_milli | UInt16 | do seed |
| budget_status | LowCardinality(String) | accepted_values ok/warn/breach/no_budget; regra inteira abaixo |
| last_cost_ts | DateTime64(9) | max(event_ts) do custo processado para o par/dia (S1) |
| projected_period_usd_micros | Int64 | projeção linear até o fim do período (informativa) |
| loaded_at | DateTime64(3) | |
Unicidade: (day_utc, team, project).

**Regra de status (S2, só inteiros, sem divisão):**
```sql
multiIf(
  budget_usd_micros = 0,                                                 'no_budget',
  toInt128(consumed_period_usd_micros) * 1000 >= toInt128(budget_usd_micros) * breach_milli, 'breach',
  toInt128(consumed_period_usd_micros) * 1000 >= toInt128(budget_usd_micros) * warn_milli,   'warn',
                                                                         'ok')
```
O cast para Int128 evita overflow (Int64 micros × 1000). `consumed_milli` não entra na decisão.

### budget_status_current
team, project, owner_team, period_start, as_of_day, as_of_ts DateTime64(9), budget_usd_micros, consumed_period_usd_micros, consumed_milli Int64, warn_milli UInt16, breach_milli UInt16, budget_status, action_on_breach, stale_threshold_hours UInt16, is_stale Bool. Unicidade: (team, project).
- Lido pelo enforcement **somente** por um role somente-leitura dedicado (grant criado pelo Alicerce).
- **`as_of_ts` (S1)** = último timestamp de custo efetivamente processado: no MVP diário, `max(last_cost_ts)` de `fct_budget_consumption_daily`, que vem de `max(event_ts)` de `int_cost_attributed` sobre o mesmo recorte que alimentou `fct_cost_chargeback_daily`. Na fase 2, é o fim da última hora fechada em `fct_budget_consumption_hourly`. É **global**, não por par: é o "high-water mark" do pipeline. Um par sem custo no dia não fica stale por isso. **Nunca** `now()`, `run_started_at` nem `loaded_at`. Assim, um run que termina bem mas não processa custo novo não "refresca" o sinal.
- `is_stale = now64() - as_of_ts > toIntervalHour(stale_threshold_hours)`; o limite vem das vars dbt `budget_stale_hours_daily` (default 26) e `budget_stale_hours_hourly` (default 2, fase 2).
- Como `is_stale` depende de `now()`, o modelo é **view** (não table), para o valor ser avaliado na leitura.
- **Segurança (S5):** `CREATE VIEW ... DEFINER = oteru_budget_view_definer SQL SECURITY DEFINER AS ...` (ClickHouse ≥ 24.4; confirmar a versão local na prova). O definer é um usuário/role sem login interativo, com **apenas** `SELECT` em `fct_budget_consumption_daily` (e, na fase 2, `fct_budget_consumption_hourly`) e nas colunas que a view lê. Não tem `SELECT` em `int_cost_attributed`, `fct_cost_*actor*`, `otel_logs` nem seeds com ator, e não tem INSERT/ALTER/DROP. O leitor `oteru_budget_reader` (§7.1) tem só `SELECT` na view. O role de alertas `oteru_budget_alerts_reader`, definido pelo Alicerce no §7.4 e usado pelo Vigia, tem `SELECT` **só** em `budget_status_current` e `audit_budget_runs`. Do lado de dados, isso exige que `audit_budget_runs` continue só com agregados (sem `actor_id`) e que nenhum dos dois roles tenha `SELECT` em `fct_*`/`int_*`. Implementação no dbt: `sql_header`/`post-hook` ou materialização custom; o adapter clickhouse não emite `DEFINER` nativamente, o que deve ser validado no plan (passo 5). A criação dos usuários e grants é coordenada com o Alicerce (§7.1) e roda fora do dbt, sem secrets no repo.

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
  - `assert_budgets_threshold_order` (S4): `0 < warn_milli < breach_milli`.
  - `assert_budgets_slug_and_no_at` (S4): team/project no slug `^[a-z0-9][a-z0-9-]{0,62}$` e nenhum `@` em nenhuma coluna.
  - `assert_budget_micros_positive_when_budgeted` (S3): 0 linhas com `budget_usd_micros < 0` ou com `budget_usd_micros = 0 AND budget_status != 'no_budget'`.
  - `assert_no_float_in_budget_contracts` (S2): `system.columns` dos modelos/seed de budget sem tipo `Float%`.
  - `assert_as_of_ts_is_cost_watermark` (S1): `as_of_ts = max(event_ts)` do recorte processado e `as_of_ts <= max(loaded_at)`.
  - `unit_tests: budget_status_thresholds` (S2), com `budget_usd_micros = 1_000_000` e warn/breach = 800/1000: consumido 799_000 → ok; 799_999 → ok; 800_000 → warn; 999_999 → warn; 1_000_000 → breach; budget 0 → no_budget. Mais um caso com micros grandes (≈ 9e15) para provar que não há overflow.
  - `assert_no_actor_columns_in_budget_models` (consulta system.columns por nome ILIKE '%actor%'), incluindo `audit_budget_runs`.
  - `assert_budget_status_current_is_stale` (unit test com as_of_ts antigo/recente vs. var).
  - **Teste de run sem custo novo (S1):** fixture com último evento em T; rodar `dbt build` com sucesso em T+27h sem inserir custo. Esperado: `as_of_ts = T` (não muda) e `is_stale = true` com `budget_stale_hours_daily = 26`. Variante: inserir um evento novo, rodar e ver `is_stale = false`.
  - `assert_audit_budget_runs_matches_status` (contagens = group by do status no run).
- Unit tests dbt (`unit_tests:`) para a lógica de status e para a virada de mês.
- **Teste de privilégio da view (S5)**, script de prova fora do dbt test, com credenciais só via env:
  1. Como `oteru_budget_reader`: `SELECT count() FROM budget_status_current` → ok.
  2. Como `oteru_budget_reader`: `SELECT * FROM fct_budget_consumption_daily LIMIT 1` e `SELECT * FROM fct_cost_chargeback_daily LIMIT 1` → erro `ACCESS_DENIED` (código 497).
  2b. Como `oteru_budget_alerts_reader` (§7.4): `SELECT` em `budget_status_current` e em `audit_budget_runs` → ok; `SELECT` em `fct_budget_consumption_daily` / `fct_cost_chargeback_daily` → `ACCESS_DENIED` (497).
  3. `SHOW CREATE VIEW budget_status_current` contém `SQL SECURITY DEFINER` e `DEFINER = oteru_budget_view_definer`.
  4. `SHOW GRANTS FOR oteru_budget_view_definer` lista só SELECT nas bases da view.
  - **Coordenação com §7.9 teste 3 (Alicerce):** os itens 1 e 2 são o lado "dados" do mesmo teste. O §7.9.3 cobre INSERT/ALTER/DROP do leitor. Os dois devem usar o mesmo nome de role e o mesmo script, sem duplicar. O §7 não é editado aqui; o Alicerce referencia este item em commit próprio.

## 6. Prova esperada
- `dbt build -s +budget_status_current` verde no ClickHouse local (sem --full-refresh), log anexado.
- Saída das queries de reconciliação (0 linhas de diferença) e de status para um fixture com 1 time em breach.
- Segunda execução incremental idêntica (hash/contagem por partição igual).
- `dbt ls --resource-type model -s budget*` + trecho do manifest mostrando contracts enforced e ausência de actor_id.
- S1: saída do teste "run sem custo novo" (as_of_ts antes/depois, is_stale=true).
- S2: saída dos unit tests de borda + `system.columns` sem Float.
- S3/S4: `dbt test -s budgets` verde e uma execução negativa com fixture inválida (warn ≥ breach, `@`, slug inválido, budget < 0) falhando como esperado, em schema/target de teste e sem alterar o seed real.
- S5: saída do script de privilégio (SELECT ok na view, ACCESS_DENIED em `fct_*`, SHOW CREATE/SHOW GRANTS). O que `oteru_budget_alerts_reader` pode `SELECT` é **exatamente como no §7.12**.

## 7. Enforcement (Alicerce)

Inspecionado no repo em `feat/48-budget-cap` @ `d49d01d` (base `e1b4f54`). Docs only; sem código neste commit.

### 7.0 Ponto de corte (o que existe hoje)

O caminho real das chamadas de IA **não passa por nenhum componente deste monorepo**. O fluxo observado é:

```
Claude Code / Cursor / outros agentes  →  API do provedor (Anthropic etc., fora do repo)
        │
        └─ telemetria OTLP (depois do custo)  →  collector :4317/:4318
              → privacy (HMAC / drop de conteúdo)  →  ClickHouse  →  dbt (#45–#47, #46)
```

| Superfície | Papel hoje | Pode cortar a chamada? |
|---|---|---|
| `oteru-emitter` | replay/forja de OTLP já capturado | não — não é o cliente LLM |
| `oteru-hooks` | ponte hook → OTLP; **invariante: nunca bloquear** (exit 0, `continue:true` / `permission:allow`) | não — contradiz o contrato do hook |
| `oteru-collector` | ingress OTLP + privacy; `filter`/`transform` só no envelope de telemetria | não — o gasto já ocorreu |
| `oteru-ingestor` | POD-of-3 OTLP → ClickHouse | não — ingestão |
| LiteLLM | perfil de *emitter* (`oteru-emitter` observa `litellm_request`); **não há proxy LiteLLM operado aqui** | não existe no repo |
| Gateway / SDK wrapper / IAM de chave | — | **ausente** |

**Conclusão:** não há ponto de corte no caminho da chamada. Collector, hooks e emitter são pós-fato ou proibidos de bloquear.

**Decisão do Vinícius (esta entrega):** **só alertar**. Gateway, `block` e as condições 6–8 **saem de escopo**. Follow-up: https://github.com/luanmorenommaciel/oteru/issues/108. A estimativa de custo/latência do `oteru-budget-enforcer` (§7.10) fica só como contexto para #108.

### 7.1 Reader

**Padrão desta entrega:** a Vigia lê o sinal via `oteru_budget_alerts_reader`, **exatamente como no §7.12**. Credencial só em secret/env. O role `oteru_budget_reader` (gateway) **não entra nesta entrega** — #108.

**Justificativa:** decisão só-alerta; o grant do alerta já está no §7.12. Ampliar o grant reabre `fct_*` e ator.

### 7.2 Latência

**Padrão desta entrega:** alerta no grão **diário** (`fct_budget_consumption_daily` → `budget_status_current`). O sinal horário da fase 2 (`fct_budget_consumption_hourly`, ≤ 1 h) continua requisito para detectar runaway mais cedo; **não** há breaker nesta entrega.

**Justificativa:** um loop de agente estoura o cap em minutos; o diário só alerta depois. Sem gateway (#108).

### 7.3 Chave do limite

**Padrão (decisão do Vinícius):** caps no grão `scope × scope_key`. `scope` ∈ {`global`, `team`, `project`, `team_project`}. `scope_key` = `'global'` (scope `global`), o slug do time (scope `team`), o slug do projeto (scope `project`), ou `'team/project'` (scope `team_project`). O `global` inclui `unassigned`. Nunca por `actor_id`. Corte por ator só via `actor_map` (#46), fora de escopo. Bloqueio por par sai para #108.

### 7.4 Ownership

| Peça | Owner | Padrão proposto | Justificativa |
|---|---|---|---|
| `alert` | **Vigia** | lê `audit_budget_runs`, `budget_status_current` e `audit_cost_runs` (lista exata de colunas no §7.12) com o role próprio `oteru_budget_alerts_reader` (§7.12), no estilo `ops/alerts/46/` | já é o dono de alerta; grant separado do `oteru_budget_reader` (#108) |
| `block` (mecanismo do gateway) | **fora desta entrega** | https://github.com/luanmorenommaciel/oteru/issues/108 | decisão do Vinícius: só alertar |
| `action_on_breach` no seed | **só `alert` nesta entrega** | validação do seed **rejeita** `block`; muda só por PR depois de #108 | decisão do Vinícius |

**Testes allowed/denied de `oteru_budget_alerts_reader`** (conectar só com esse role; lista do §7.12 v3 **mais** `scope_key` — a Vigia atualiza o §7.12 em patch próprio):

- **Allowed:** `SELECT` de **todas** as colunas de `audit_budget_runs` (incluindo `scope` e `scope_key`); de `budget_status_current` só `scope`, `scope_key`, `team`, `project`, `as_of_ts`, `is_stale`, `budget_status`, `action_on_breach`; de `audit_cost_runs` só `run_id`, `run_completed_at`, `run_status`, `events_dropped_late`.
- **Denied:** `SELECT` de qualquer outra coluna de `budget_status_current` ou de `audit_cost_runs`; `SELECT` em qualquer `fct_*`, seeds ou `otel_*`; `INSERT`/`ALTER`/`DROP` em qualquer relação.

Valores dos budgets são configuráveis (warn 80% / breach 100% = `warn_milli=800` / `breach_milli=1000` até o Vinícius fixar os números do seed).

### 7.5 Dados stale

**DECIDIDO pelo Vinícius:** **fail-open + alerta** quando `is_stale = true` (via a coluna `is_stale` da view). Vars: `budget_stale_hours_daily = 26`, `budget_stale_hours_hourly = 2` (fase 2). Fail-open nesta entrega = **não** há recusa de chamada (não há gateway); a Vigia alerta (A2/A3).

**Justificativa:** um dbt/CH indisponível não pode ser tratado como block. 26 h cobre um dia UTC + folga do cron; 2 h cobre o SLA de ≤ 1 h da fase 2 com margem de um ciclo.

### 7.6 Reversão

**Padrão proposto:** sair de `warn`/`breach` = (a) PR no seed `budgets` com **override de vigência** (`valid_from`/`valid_to`) ou (b) expiração automática na **virada do mês UTC** (`period_start`). **Nenhuma edição manual em produção**.

**Justificativa:** o seed é a fonte versionada; override com vigência é o mecanismo de §3.1.

### 7.7 `unassigned`

**DECIDIDO pelo Vinícius:** custo `unassigned` **entra no cap `scope='global'`** (o global soma todo o custo, inclusive sem time/projeto). Os caps `team` / `project` / `team_project` não atribuem `unassigned` a um time.

**Justificativa:** sem isso o volume sem atribuição some do radar; o global fecha o buraco. Sem `block` nesta entrega.

**Teste de aceite:** fixture com custo `unassigned` e um cap `global`; `consumed_*` do `scope='global'` inclui esse custo; A1 dispara se o global cruzar 80%/100%. Um cap `team`/`team_project` de outro time **não** sobe por causa do `unassigned`.

### 7.8 Agendamento

**Padrão proposto:** o mesmo scheduler das runs da **#103** (já decidido, §9.2). Nenhuma infra nova de cron/orquestração neste card. Qualquer custo novo de compute (job extra, gateway 24/7, fila) vai **primeiro ao Maestro**.

**Justificativa:** um segundo scheduler diverge lookback/idempotência de #45–#47 e #103. Os alertas leem a view já materializada pelo run existente.

### 7.9 Testes de aceite do enforcement

Complementam os CA de dados. Só alerta + grants; não exigem `--full-refresh`. Testes de gateway/`block` → #108.

1. **Só alerta.** Seed e view só aceitam `action_on_breach='alert'`. Não há recusa de chamada. A Vigia lê `audit_budget_runs` no `warn`/`breach` (A1) nos quatro `scope`.
2. **Seed rejeita `block`.** Fixture com `action_on_breach='block'` (ou valor fora de `{alert}`) falha a validação do seed / guard. O seed real não muda.
3. **Stale → fail-open + alerta (DECIDIDO).** `as_of_ts` mais velho que `budget_stale_hours_daily` (26) ⇒ `is_stale`; nenhuma chamada é recusada; A2/A3 disparam.
4. **`unassigned` conta no `global`.** Ver teste de §7.7.
5. **Grants `oteru_budget_alerts_reader`.** Allowed/denied exatamente como §7.4 / §7.12.
6. **Segredo fora do repo.** `gitleaks` + `check_pii` + grep de DSN/password/token em `docs/`, `.github/workflows/`, seeds e evidências: zero hits. Credencial só em env/secret.
7. **Reader não vê ator.** Result set permitido não tem `actor_id` nem `hmac:v1:` (alinha CA5).
8. **Revert de status.** Override no seed com vigência nova, no próximo run #103, tira a linha do `breach`/`warn` sem `ALTER` manual; virada de `period_start` zera o acumulado (CA3).

### 7.10 Estimativa de custo e latência do `oteru-budget-enforcer` (contexto para #108)

**Fora desta entrega.** Vinícius escolheu só alertar; o gateway não se constrói aqui. O texto abaixo permanece só como contexto para https://github.com/luanmorenommaciel/oteru/issues/108.

Nenhum número abaixo é medido em produção. O que o repo **de fato** implanta hoje: só **Docker Compose** local (`oteru-collector/docker-compose.yml` + overrides). **Não há** k8s, Helm nem Terraform neste monorepo. O collector é imagem local (`oteru-collector:0.155.0`), sem registry. ClickHouse no override é `clickhouse/clickhouse-server:latest` no mesmo compose network. O README do collector documenta um *exemplo* (não commitado como `docker-compose.limits.yml`) de `deploy.resources.limits.memory: 512M` no collector e 2G no ClickHouse, e diz que o sandbox esperado é collector ~50–150 MB — **não** é sizing de deploy compartilhado.

**Comparação alert-only:** se Vinícius escolher só alerta (§9 item 1), **custo de infra nova ≈ 0**. Reusa ClickHouse + dbt do scheduler #103 + consultas Vigia no estilo `ops/alerts/46/`. Sem gateway, sem hop, sem chave de provedor neste componente.

#### Premissas (explícitas)

| Premissa | Valor adotado | Origem |
|---|---|---|
| Região de referência | `us-east-1` (N. Virginia) | região mais comum nas tabelas públicas AWS; **não** está no repo |
| Forma de deploy se houver `block` | 2 processos 24/7 (par para não ter um único PID), no mesmo padrão compose (um serviço, N réplicas) ou 2 VMs equivalentes | compose existe; HA em k8s **não** existe aqui |
| Tamanho | 2 vCPU / 2–4 GiB por réplica (`t3.small` ou `t3.medium`) | o único teto documentado no repo é 512 MiB de *exemplo* para o collector; o SKU público imediatamente acima de 512 MiB com 2 GiB é `t3.small`; `t3.medium` (4 GiB) é a margem se o proxy + cache + TLS crescer |
| Disco | 8 GiB gp3 por réplica (boot) | mínimo usual de boot; **não** medido |
| Horas/mês | 730 (= 24 × 365 / 12) | convenção de fatura mensal; não é 30×24 |
| Volume de requests | **desconhecido** | o sample commitado tem 523 batches; o fixture de custo tem 10 `claude_code.api_request` (9 visíveis). Nenhum é carga de produção. A faixa de custo **não** escala com QPS neste desenho (cache in-process; compute é 24/7 fixo) |
| Refresh do cache | a cada **5 min** (`N = 5`) | hipótese: entre o MVP diário e o sinal horário (≤ 1 h); o hot path **não** consulta o warehouse |
| Tráfego / NAT / load balancer / IPv4 público | fora da conta | preço **a confirmar** (não dimensionado; o compose publica portas no host, sem ELB) |
| Créditos T3 Unlimited se CPU média > baseline | fora da conta | $0.05 / vCPU-hora na página T3; **a confirmar** se o proxy ficar hot |

#### Custo (lista pública, consultada 2026-10-21)

Fontes: [Amazon EC2 T3](https://aws.amazon.com/ec2/instance-types/t3/) (Linux/Unix, On-Demand, US East N. Virginia: `t3.small` **$0.0209/h**, `t3.medium` **$0.0418/h**); [Amazon EBS Pricing](https://aws.amazon.com/ebs/pricing/) e [EBS volume types](https://aws.amazon.com/ebs/volume-types/) (gp3 **$0.08/GB-mês** “in a region that charges $0.08”; a página de tipos lista $0.08/GB-month). Página geral: [EC2 On-Demand](https://aws.amazon.com/ec2/pricing/on-demand/). Impostos e descontos RI/SP fora.

Derivado (2 réplicas × 730 h):

- Compute: 2 × 730 × $0.0209 = **$30.51/mês** (`t3.small`) até 2 × 730 × $0.0418 = **$61.03/mês** (`t3.medium`).
- Disco: 2 × 8 GiB × $0.08 = **$1.28/mês**.
- **Faixa compute+disco: ~$32–$62/mês.** LB, transferência, NAT, observabilidade extra: **a confirmar**.
- 1 réplica (só sandbox, sem HA): metade do compute+disco (~$16–$31/mês) — **não** recomendado se `block` estiver no caminho.

Se o deploy ficar no mesmo compose local do collector (dev), o custo cloud é **$0**; a faixa acima só vale se alguém promover o enforcer a VM/cloud. Qualquer custo novo vai ao Maestro (§7.8) **antes** de provisionar.

#### Latência adicionada (por request, ordem de grandeza — não há load test neste repo)

Hot path: lookup in-process no cache de `budget_status_current` (mapa `team×project` → status) + **um hop de rede** até o proxy + forward ao provedor.

- Lookup em memória: muito abaixo de 1 ms (estrutura pequena: 1 linha por par; sem ida ao ClickHouse).
- Hop extra na mesma região / mesmo host compose: tipicamente **1–10 ms p50**; p99 sobe com handshake TLS frio, perda leve e scheduling. Hipótese de trabalho: **p50 2–15 ms**, **p99 15–50 ms** no mesmo AZ/host. Cliente laptop → gateway público → vendor: o hop extra pode ser **10–80 ms p50** e **50–200 ms p99** (RTT WAN). **Não medido.**
- Refresh a cada 5 min é **fora** do hot path (goroutine/timer). Se o refresh falhar, vale `is_stale` / fail-open (§7.5), não um SELECT síncrono no warehouse.
- Streaming/SSE: o proxy precisa fazer *pass-through* de stream (não bufferar a completion). Latência de *time-to-first-token* herda o hop acima; bufferar quebraria o cliente. **Não implementado; risco da spec futura.**

#### Riscos deste desenho

- **SPOF:** um gateway único no caminho de todas as chamadas. Outage do gateway ⇒ **fail-open** (alinha §7.5) + alerta Vigia; senão para o org inteiro. Dois processos reduzem o SPOF de PID, não o de “todo mundo aponta para este DNS”.
- **Streaming/SSE:** sem pass-through, o corte `block` ou o proxy quebram Claude/Cursor em modo stream.
- **Credencial do provedor no gateway:** a chave (Anthropic etc.) só em secret/env, nunca no repo, nunca em log. Request/response body e prompts **não se logam**. Gateway **não é público** (rede interna / allowlist). Ver §7.11.
- **Bypass:** quem chama o vendor direto não passa pelo corte — §7.11 item 8.

### 7.11 Gateway / condições 6–8

**Fora desta entrega.** Condições 6–8 (credencial por par, segredo do provedor, bypass) e o `oteru-budget-enforcer` vivem em https://github.com/luanmorenommaciel/oteru/issues/108. Precisam de spec própria + revisão da Sentinela **antes de qualquer código**.

**Guard do seed (esta entrega, dados):** `scripts/check_chargeback_seeds.py` será estendido para `seeds/budgets.csv`: `@` proibido em qualquer coluna; regex de slug (`^[a-z0-9][a-z0-9-]{0,62}$`) **só** em `team`/`project` **não vazios**; **nenhum** `*` em qualquer coluna; `scope` ∈ {`global`, `team`, `project`, `team_project`}. `hmac:vN` não se aplica (não há `actor_id`).

### 7.12 Alertas (Vigia)

Entrega em `ops/alerts/48/`: SQL + `run_alert_tests.sh` (clickhouse-local, casos ok/dispara/limite/histórico curto) + evidência com sha256, igual a `ops/alerts/46/`. Só leitura; nenhum alerta escreve em tabela.

| # | Alerta | Fonte | Dispara quando |
|---|---|---|---|
| A1 | `budget_warn_breach_new` | `audit_budget_runs` (último run com sucesso) | uma linha (`scope`, `team`, `project`) entra em `warn` (80%) ou `breach` (100%) e não estava nesse status no run anterior; vale para os 4 escopos (`global`, `team`, `project`, `team_project`); o `global` inclui `unassigned` |
| A2 | `budget_status_stale` | `budget_status_current` | alguma linha com `is_stale = true`; é o sinal que sustenta o fail-open (§7.5). Depende de `as_of_ts` medir o último custo processado, não a hora do run (condição 1 da Sentinela) |
| A3 | `budget_run_missing` | `audit_budget_runs` | nenhum run com sucesso há mais de `budget_stale_hours_daily` (26h; 2h na fase 2) |
| A4 | `cost_late_beyond_lookback` | `audit_cost_runs.events_dropped_late` (contrato #45/#47, só leitura, sem mudança) | soma no dia UTC fechado > 0, ou seja, custo chegou com mais de 48h e ficou fora do consumo (§8) |
| A5 | `budget_gateway_bypass` | **fora desta entrega** (Vinícius: só alertar, sem gateway) | vai para a issue de follow-up do bloqueio, junto com as condições 6–8 |

**Grant (privilégio mínimo):** role `oteru_budget_alerts_reader`, separado do `oteru_budget_reader`. `SELECT` só em `audit_budget_runs` (todas as colunas, incluindo `scope`; só agregados), `budget_status_current` (colunas `scope`, `team`, `project`, `as_of_ts`, `is_stale`, `budget_status`, `action_on_breach`) e `audit_cost_runs` (`run_id`, `run_completed_at`, `run_status`, `events_dropped_late`). Sem `fct_*`, seeds, `otel_*`, `INSERT`/`ALTER`/`DROP`. Mesma regra de `SQL SECURITY DEFINER` da condição 5. Credencial só em secret/env.

**Testes allowed/denied** (iguais aos de §7.4): allowed = cada coluna da lista acima; denied = qualquer outra coluna de `budget_status_current` ou de `audit_cost_runs`, qualquer `fct_*`, seeds, `otel_*`, e `INSERT`/`ALTER`/`DROP`.

**Sem ator:** nenhum alerta lê ou emite `actor_id`, hmac ou `@`; a saída é só scope × team × project × status × contagem.

## 8. Riscos
- **Granularidade**: o diário (MVP) detecta um agente em loop só depois; o horário (fase 2, ≤ 1h) é o que de fato atende "runaway".
- int_cost_attributed é view com actor_id; modelos horários devem agregar antes de materializar para não vazar ator.
- Lookback 48h: custo que chegar com > 48h de atraso não entra no consumo (subestima). Precisa de monitor de atraso.
- Mudança retroativa de atribuição (service_map/actor_map) move custo entre times dentro do período e pode trocar status sem novo gasto.
- Seed como fonte de budgets: mudança exige PR/deploy; ok para início, pode não servir a donos de budget não-técnicos.
- Ponto flutuante eliminado (S2). Risco residual: overflow em `micros*1000`, mitigado com Int128 e unit test.
- `SQL SECURITY DEFINER` depende da versão do ClickHouse e de hook/materialização custom no dbt-clickhouse (S5). Se não for suportado localmente, a prova falha e isso bloqueia o merge, sem fallback para grant direto em `fct_*`.
- `as_of_ts` global (S1): um par sem tráfego não é stale, mas uma fonte parcial (um único serviço ainda emitindo) mascara a falha das outras. Proposta futura: watermark por service_name no Vigia.

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
