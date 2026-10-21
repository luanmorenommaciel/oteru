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

**Proposta de corte (a construir, owner Alicerce, só se Vinícius escolher `block` — §9 item 1):** um gateway HTTP novo (`oteru-budget-enforcer`, fora do collector) **à frente do provedor**. Lê `budget_status_current` por `oteru.team` × `oteru.project` (header ou resource attrs) e devolve allow/deny **antes** do POST ao vendor. Quem não passar pelo gateway (Claude Code nativo contra `api.anthropic.com`) **não pode ser bloqueado** — só alerta (Vigia). Não usar hooks como corte. Não filtrar no collector: isso esconderia telemetria e não evitaria a fatura.

O MVP diário é **alert-only na prática** até o gateway existir e até a decisão de política do §9 item 1. `action_on_breach='block'` no seed fica declarado, mas o mecanismo só age quando o gateway estiver no caminho.

### 7.1 Reader

**Padrão proposto:** o enforcement lê **somente** a view `budget_status_current`, com um role ClickHouse dedicado somente-leitura (`oteru_budget_reader`) que tem `SELECT` **apenas** nessa view (sem `SELECT` em `otel_logs`, seeds, `fct_*`, `audit_*`, sem `INSERT`/`ALTER`/`DROP`). Credencial (`CLICKHOUSE_BUDGET_READER_DSN` ou equivalente) só em secret/env (mesmo padrão de `CLICKSTACK_API_KEY` / `.env` gitignorado). Nunca em código, workflow, evidência ou seed.

**Justificativa:** o contrato da view já é o sinal (`team`, `project`, `budget_status`, `action_on_breach`, `is_stale`, `as_of_ts`). Ampliar o grant reabre PII/`actor_id` e tabelas de fato. Segredo no repo quebra a disciplina de credenciais do monorepo.

### 7.2 Latência

**Padrão proposto:** MVP = status **diário** (`fct_budget_consumption_daily` → `budget_status_current`). Enforcement **real** (bloquear runaway) depende do sinal horário da fase 2 (`fct_budget_consumption_hourly`), latência **≤ 1 h**. Até a fase 2, o breaker no máximo alerta com atraso de até um dia UTC.

**Justificativa:** um loop de agente estoura o cap em minutos; o grão diário só vê depois. A spec §8 e o intent já marcam o horário como requisito, não como extra.

### 7.3 Chave de corte

**Padrão proposto:** corte **somente** `team × project` (as duas colunas da view). Nunca por `actor_id`, e-mail, `user.id` ou service_name isolado. Corte por ator só via regras já existentes do `actor_map` (#46), **fora de escopo** deste card.

**Justificativa:** CA5 e §9.2; agregado permanente de #46 não carrega ator. Cortar por pessoa seria re-identificação e foge do grão do budget.

### 7.4 Ownership

| Peça | Owner | Padrão proposto | Justificativa |
|---|---|---|---|
| `alert` | **Vigia** | lê `audit_budget_runs` e `budget_status_current` (só `team`, `project`, `as_of_ts`, `is_stale`) com o role próprio `oteru_alert_reader` (§7.12), no estilo `ops/alerts/46/` | já é o dono de alerta; grant separado do `oteru_budget_reader` do gateway |
| `block` (mecanismo do gateway) | **Alicerce** | implementa o reader + o corte no gateway proposto em §7.0 | o ponto de corte é infra, não dbt |
| `action_on_breach` no seed | política | muda **só por PR**, com **OK do Vinícius** | §3.1 e §9.2; troca alert↔block é decisão de produto, não de plantão |

Valores de budget e a escolha breaker=`block` vs só-alerta são **decisão de política do Vinícius** (§9 itens 1 e 4). Este documento não os fixa.

### 7.5 Dados stale

**Padrão proposto (provisório):** **fail-open + alerta** quando `is_stale = true`. Vars: `budget_stale_hours_daily = 26`, `budget_stale_hours_hourly = 2` (fase 2). Fail-open = o gateway **não** bloqueia; a Vigia alerta (scheduler parado, lookback furado, ClickHouse down).

**Justificativa:** um dbt/CH indisponível não pode parar todos os times. 26 h cobre um dia UTC + folga do cron; 2 h cobre o SLA de ≤ 1 h da fase 2 com margem de um ciclo.

**DECISÃO DE POLÍTICA para o Vinícius (§9 item 2):** fail-open (aqui proposto) vs fail-closed. Se escolher fail-closed, o default deste §7.5 inverte e o teste de aceite stale muda.

### 7.6 Reversão

**Padrão proposto:** reverter bloqueio = (a) PR no seed `budgets` com **override de vigência** (`valid_from`/`valid_to`) ou (b) expiração automática na **virada do mês UTC** (`period_start`). **Nenhuma edição manual em produção** (nem `ALTER` na view, nem UPDATE no seed aplicado no CH).

**Justificativa:** o seed é a fonte versionada; override com vigência é o mecanismo já descrito em §3.1. Edição no cluster foge de auditoria e de reconciliação.

### 7.7 `unassigned`

**Padrão proposto:** custo `unassigned` **fica fora** dos caps de team × project. Entra só num **cap global de alerta** (seed com `team='unassigned'`, `project='unassigned'`, `action_on_breach='alert'`). Não bloqueia ninguém: `unassigned` mistura times sem `oteru.team`/`service_map`.

**Justificativa:** bloquear `unassigned` puniria tráfego ainda sem atribuição (fase 2 de #46 incompleta, slug drop, seed vazio). Alertar o volume global evita buraco invisível.

**DECISÃO DE POLÍTICA para o Vinícius (§9 item 3):** fora dos caps de time + cap global de alerta (aqui proposto) vs incluir `unassigned` em algum cap de block.

### 7.8 Agendamento

**Padrão proposto:** o mesmo scheduler das runs da **#103** (já decidido, §9.2). Nenhuma infra nova de cron/orquestração neste card. Qualquer custo novo de compute (job extra, gateway 24/7, fila) vai **primeiro ao Maestro**.

**Justificativa:** um segundo scheduler diverge lookback/idempotência de #45–#47 e #103. Gateway e reader usam a view já materializada pelo run existente.

### 7.9 Testes de aceite do enforcement

Complementam CA1–CA8 (dados). São do mecanismo Alicerce + grants + Vigia; não exigem `--full-refresh`.

1. **Breach → block só naquele team × project.** Fixture: time A/projeto X em `breach` com `action_on_breach='block'`; time B/projeto Y em `ok`. O gateway (ou o stub do reader) recusa A×X e aceita B×Y e A×Z. Nenhum outro par muda.
2. **Stale → fail-open + alerta** (enquanto §9 item 2 não inverter). `as_of_ts` mais velho que `budget_stale_hours_daily` (26) ⇒ `is_stale`; gateway permite a chamada; Vigia dispara em `audit_budget_runs` / ausência de run fresco. Com `as_of_ts` fresco e `breach`+`block`, o caso 1 continua válido.
3. **Role somente-leitura não escreve.** Conectar com `oteru_budget_reader` e tentar `INSERT`/`ALTER`/`DROP`/`SELECT` fora de `budget_status_current` falha. `SELECT` na view passa.
4. **Segredo fora do repo.** `gitleaks` + `check_pii` + grep de DSN/password/token em `docs/`, `.github/workflows/`, seeds e evidências: zero hits. Credencial só em env/secret.
5. **Reader não vê ator.** Result set de `budget_status_current` não tem coluna `actor_id` nem valor `hmac:v1:` (alinha CA5).
6. **Alert-only sem gateway.** Com `action_on_breach='alert'` (ou gateway ausente), não há recusa; a Vigia ainda lê `audit_budget_runs` no breach.
7. **Revert.** Override no seed com vigência nova, no próximo run #103, tira o par do `breach` sem `ALTER` manual; virada de `period_start` zera o acumulado (CA3) e encerra o block do mês anterior.

### 7.10 Estimativa de custo e latência do `oteru-budget-enforcer`

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

### 7.11 Condições para a spec futura do gateway (Sentinela)

O `oteru-budget-enforcer` **precisa de spec própria + revisão de segurança da Sentinela antes de qualquer código.** Este §7 só amarra o contrato de dados e as condições abaixo. Não implementar o gateway neste card.

6. **Identidade team × project.** Vem de uma **credencial do gateway por par team×project** (token/mTLS emitido para aquele par), **nunca** de headers declarados pelo cliente (`X-Oteru-Team` etc. são ignorados). Par ausente, malformado, revogado ou que não case com a credencial ⇒ **rejeitar sem forward ao provedor** (proposta: HTTP 401 se a credencial falta/é inválida; HTTP 403 se a credencial é válida mas o par está em `breach`+`block`). A resposta de recusa **não** inclui `consumed_*`, `budget_*` nem status de **outros** pares.
7. **Segredo e exposição.** Chave do provedor só em secret/env (mesmo padrão `OTERU_PII_HMAC_KEY` / `.env` gitignorado). Nunca logar body de request/response nem prompts. Gateway não publicado na internet. Fail-open também em **outage do gateway** (além de `is_stale`, §7.5), com **SLO de latência** a fixar na spec do gateway (hipótese de trabalho: se o hop extra p99 exceder 50 ms no mesmo host/AZ de forma sustentada, ou healthcheck falhar, fail-open + alerta — número do SLO não é medido aqui). Recusa nunca revela budget/consumo de outro par.
8. **Bypass.** Quem tem a chave do provedor e chama `api.anthropic.com` (ou equivalente) **direto** — o default do Claude Code e de qualquer SDK sem apontar o proxy — **fura o block**. Documentar na spec do gateway: o corte só vale para tráfego que usa a credencial do par e o endpoint do enforcer. A Vigia alerta sobre esse tráfego: linhas em `fct_cost_chargeback_daily` / `int_cost_attributed` cujo `team×project` não tem request correspondente no enforcer no mesmo lookback (ou `service_name` conhecido de cliente que não passou pelo proxy). Sem gateway no caminho, o default é esse bypass — por isso o MVP é alert-only até a spec+review.

Na fase de implementação dos **dados** (não do gateway), o seed guard (`scripts/check_chargeback_seeds.py`) será estendido para `seeds/budgets.csv`: `@` proibido; slugs de `team`/`project`/`owner_team` no mesmo espírito de `oteru.team`/`oteru.project` (`^[a-z0-9][a-z0-9-]{0,62}$`, salvo `unassigned` e o `*` se a decisão de §9.1 permitir). `hmac:vN` não se aplica (não há `actor_id` no seed de budget).

### 7.12 Alertas (Vigia)

Entrega em `ops/alerts/48/`: SQL + `run_alert_tests.sh` (clickhouse-local, casos ok/dispara/limite/histórico curto) + evidência com sha256, igual a `ops/alerts/46/`. Só leitura; nenhum alerta escreve em tabela.

| # | Alerta | Fonte | Dispara quando |
|---|---|---|---|
| A1 | `budget_warn_breach_new` | `audit_budget_runs` (último run com sucesso) | um team × project entra em `warn` ou `breach` e não estava nesse status no run anterior; `breach` com `action_on_breach='block'` sem gateway também dispara |
| A2 | `budget_status_stale` | `budget_status_current` | alguma linha com `is_stale = true`; é o sinal que sustenta o fail-open (§7.5). Depende de `as_of_ts` medir o último custo processado, não a hora do run (condição 1 da Sentinela) |
| A3 | `budget_run_missing` | `audit_budget_runs` | nenhum run com sucesso há mais de `budget_stale_hours_daily` (26h; 2h na fase 2) |
| A4 | `cost_late_beyond_lookback` | `audit_cost_runs.events_dropped_late` (contrato #45/#47, só leitura, sem mudança) | soma no dia UTC fechado > 0, ou seja, custo chegou com mais de 48h e ficou fora do consumo (§8) |
| A5 | `budget_gateway_bypass` | só se o gateway existir (§7.11 item 8) | custo por team × project em `fct_cost_chargeback_daily` sem request correspondente no enforcer no mesmo lookback |

**Grant (privilégio mínimo):** role `oteru_alert_reader`, separado do `oteru_budget_reader`. `SELECT` só em `audit_budget_runs`, `budget_status_current` (colunas `team`, `project`, `as_of_ts`, `is_stale`, `budget_status`, `action_on_breach`) e `audit_cost_runs` (`run_id`, `run_completed_at`, `run_status`, `events_dropped_late`). Sem `fct_*`, seeds, `otel_*`, `INSERT`/`ALTER`/`DROP`. Mesma regra de `SQL SECURITY DEFINER` da condição 5. Credencial só em secret/env. Teste: o SELECT nas colunas permitidas passa e o SELECT em `fct_*` falha.

**Sem ator:** nenhum alerta lê ou emite `actor_id`, hmac ou `@`; a saída é só team × project × status × contagem.

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
