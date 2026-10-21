# Spec #46: Chargeback de custo (time / owner / projeto)

Base: `origin/feat/45-47-cost-aggregates` @ `27ba4c36b5a1aa572ef36996e80d083a5602e1ef`. Engine: ClickHouse + dbt-clickhouse.

## 0. O que existe hoje (lido no repo)
- `otel.stg_api_request` (`oteru-collector/clickhouse/views/stg_api_request.sql`) expõe `service_name` (de `ServiceName`), `actor_id` (de `LogAttributes['user.email']`, já HMAC) **e** o mapa `ResourceAttributes` inteiro.
- `int_api_request_dedup` seleciona `service_name` e `actor_id`, mas **não** propaga `ResourceAttributes`.
- `fct_cost_daily` / `fct_cost_actor_daily`: não propagam `service_name`.
- Atributos de recurso encontrados no código: `service.name` (hooks: `"<ide>-hooks"`; emitter/factories: `claude-code`, `support-agent`, `orders-mcp-server`…) e `oteru.traffic`. **Nenhum** atributo `oteru.team`, `oteru.project`, `oteru.owner` ou repo existe hoje no collector, emitter ou hooks.
- Seed existente para padrão de mapeamento: `seeds/model_aliases.csv` + `COALESCE(..., 'unmapped')` em `fct_cost_daily`.

## 1. Origem de team/owner/projeto

**Decisão: ambos, com precedência explícita, mapeamento declarado como caminho principal na fase 1.**

Ordem de precedência (primeira não vazia vence), por request deduplicado:
1. `ResourceAttributes['oteru.team']` / `['oteru.project']` vindos de `OTEL_RESOURCE_ATTRIBUTES` (fase 2, owner Duto; aprovada 2026-10-21).
2. Seed `chargeback_service_map` keyed por `service_name` (não-PII).
3. Seed `chargeback_actor_map` keyed por `actor_id` HMAC (opcional, exceção).
4. `unassigned` (literal exato) para `team`, `project` e `owner`.

`owner` = time responsável (coluna `owner_team` no seed), não pessoa; nunca guardamos nome/e-mail de owner.

`attribution_source` registra qual regra venceu: `'otel_attr' | 'service_map' | 'actor_map' | 'unassigned'`.

**Custo não atribuído:** nunca é descartado (LEFT JOIN + COALESCE para `'unassigned'`). Totais por dia reconciliam exatamente com `fct_cost_daily` (teste abaixo).

Alternativas consideradas:
- *Só atributos OTel*: mais preciso e auto-declarado, mas hoje não existe nenhum atributo de time; tudo cairia em `unassigned` até Duto mudar emitter/hooks. Rejeitado como única fonte.
- *Só mapeamento declarado*: funciona já, mas `service_name` é grosso (vários times podem usar `claude-code`). Rejeitado como única fonte.
- *Mapeamento por e-mail/username*: **proibido** (re-identificação, PII em repo).
- *Descartar não atribuído*: rejeitado — quebra a reconciliação e esconde custo.

## 2. Reconciliação com `actor_id` HMAC sem re-identificação
- Chaves permitidas em mapeamentos: `service_name`, atributos de recurso não-PII (`oteru.team`, `oteru.project`) e `actor_id` no formato `^hmac:v1:[0-9a-f]{64}$`. **Nunca** e-mail, nome, login, ou qualquer identificador em claro; o arquivo de mapeamento **nunca** armazena originais (nem em comentários).
- Caminho preferido: `service_name` ou atributos `oteru.team`/`oteru.project` em `OTEL_RESOURCE_ATTRIBUTES`. `chargeback_actor_map` é exceção.
- **Ferramenta self-hmac (owner: Duto, comando administrativo da fase 2):** script local que lê a chave **somente** da env `OTERU_PII_HMAC_KEY` e o e-mail **somente** do stdin (nunca argv, para não ir ao histórico do shell). Falha fechada (exit ≠ 0, stdout vazio) se a chave estiver ausente ou curta (< 32 bytes). Em sucesso imprime **apenas** `hmac:v1:<64hex>` e nada mais; não loga nada (nem e-mail, nem chave, nem hash) em stderr, arquivo ou syslog. A chave **nunca** fica no repo nem em CI (testes usam chave de teste gerada no próprio teste). **Execução restrita ao Vinícius e aos administradores do collector** (decisão do Vinícius, 2026-10-21); a restrição é documentada no comando e a chave nunca fica no repo/CI. Até a ferramenta existir, `chargeback_actor_map` entra vazio (só header).
- **Rotação de chave (`v1`→`v2`) não pode falhar em silêncio:** linhas do `chargeback_actor_map` com prefixo de versão diferente do corrente (`hmac:v1:`) continuam caindo em `unassigned` no join, mas são detectadas: (i) teste dbt `test_chargeback_actor_map_hmac_version` no seed, `severity: warn` por padrão e `error` via `var('chargeback_strict_hmac_version', false)`; (ii) métrica em `audit_cost_runs` (ver §4) com alerta da Vigia quando > 0.
- **TTL:** toda tabela por ator herda `day_utc + INTERVAL {{ var('actor_cost_ttl_days') }} DAY` (90). Agregados por team/projeto **sem** `actor_id` podem ser permanentes como `fct_cost_daily`. Motivo: minimização de dados (guardrail 5 já exige isso) e chargeback histórico precisa de série longa por time, não por pessoa. Um seed `chargeback_actor_map` é dado de referência (não fato), mas por conter pseudônimos também deve ser revisado por Sentinela.
- **Decisão: `actor_map` nunca entra na tabela permanente.** Linhas com `attribution_source='actor_map'` existem apenas em `int_cost_attributed` e `fct_cost_chargeback_actor_daily` (TTL 90d). Em `fct_cost_chargeback_daily` esse custo é somado em `team='unassigned'`, `project='unassigned'`, `owner_team='unassigned'`, `attribution_source='unassigned'`. Assim nenhum agregado permanente pode equivaler a dado individual (ex.: time unipessoal), e a reconciliação exata com `fct_cost_daily` se mantém.

## 3. Mudanças de contrato
**Nenhuma mudança em contratos existentes** (`fct_cost_daily`, `fct_cost_actor_daily`, `int_api_request_dedup`, `stg_api_request`): mesmas colunas, grão, engine e TTL. Fase 1 é 100% aditiva.

Fase 2 (atributos OTel) exige propagar `ResourceAttributes['oteru.team'|'oteru.project']`. Opções:
- (a) adicionar colunas `team_attr`, `project_attr` em `int_api_request_dedup` — aditivo, não quebra consumidores, mas altera modelo existente → **aprovado pelo Vinícius em 2026-10-21**;
- (b) novo modelo `int_api_request_attrs` (`dedup_key → team_attr, project_attr`) lendo do source com o mesmo lookback — sem tocar no existente, mas duplica lógica de dedup.
Recomendação: (a), marcado **aditivo, aprovado pelo Vinícius em 2026-10-21**. Nada é `breaking`.

### Fase 2 — atributos OTel (owner: Duto)
- O emitter lê `oteru.team` / `oteru.project` de `OTEL_RESOURCE_ATTRIBUTES` como **resource attributes** (mecanismo padrão do SDK). **Nenhuma mudança nos hooks** (`oteru-hooks/oteru_hook.py`); o collector apenas repassa (pass-through).
- `oteru-collector/privacy/policy.json`: allowlist de `oteru.team` e `oteru.project` com regex de slug `^[a-z0-9][a-z0-9-]{0,62}$`.
- Valor inválido (incluindo qualquer `@`): o collector descarta **somente o atributo**, mantém o registro; o custo desse registro cai em `unassigned`.
- Testes (Duto): (1) valor válido passa intacto até `otel_logs`; (2) valor com `@` é removido e o registro permanece; (3) atributo ausente → custo em `unassigned`; (4) com `oteru.team` inválido, contagem de registros e `sum(cost_usd_micros)` são iguais aos do mesmo envio sem o atributo.

## 4. Novos objetos
### Seeds
`seeds/chargeback_service_map.csv`: `service_name String, team String, project String, owner_team String, valid_from Date`. Unique: `service_name` (fase 1 sem versionamento temporal; `valid_from` reservado).
`seeds/chargeback_actor_map.csv`: `actor_id String, team String, project String`. Unique `actor_id`; regex `^hmac:v1:[0-9a-f]{64}$`.

### `int_cost_attributed` (view ou incremental, 48h lookback)
Grão: 1 linha por `dedup_key`. Colunas: `dedup_key String, event_ts DateTime64(9), day_utc Date, actor_id String, service_name String, team String, project String, owner_team String, attribution_source LowCardinality(String), cost_usd_micros Int64, input_tokens Int64, output_tokens Int64, cache_read_tokens Int64, cache_creation_tokens Int64`.

### `fct_cost_chargeback_daily` — permanente, sem `actor_id`
Grão: `day_utc × team × project × owner_team × attribution_source`. Engine `ReplacingMergeTree()`, `order_by (day_utc, team, project, owner_team, attribution_source)`, `unique_key` igual, `delete+insert`, lookback `cost_lookback_hours`, **sem TTL**.
Colunas: `day_utc Date, team String, project String, owner_team String, attribution_source LowCardinality(String), cost_usd_micros Int64, cost_usd Float64, requests UInt64, input_tokens Int64, output_tokens Int64, cache_read_tokens Int64, cache_creation_tokens Int64`.

### `fct_cost_chargeback_actor_daily` — por ator, TTL 90d
Grão: `day_utc × team × project × actor_id`. Mesmas métricas + `actor_id String`. `ttl: "day_utc + INTERVAL {{ var('actor_cost_ttl_days') }} DAY"`. (Opcional; só se líderes precisarem de drill-down por pseudônimo.)

### Métricas em `audit_cost_runs` (escrita: Modelo; critério/alerta: Vigia, sem código da Vigia neste intent)
- `actor_map_hmac_version_mismatch_rows`: a cada run, nº de linhas do seed `chargeback_actor_map` cujo prefixo `hmac:vN:` difere da versão corrente (`v1`). Alerta Vigia se > 0.
- `unassigned_share_by_source`: por dia e `attribution_source`, participação do custo em `unassigned`. Alerta Vigia se subir > 5 p.p. em relação à mediana dos 7 dias anteriores.
- Gravadas de forma aditiva (novas linhas/colunas de métrica; sem alterar colunas existentes de `audit_cost_runs`).

### Contratos e testes dbt
- `contract: enforced: true` nos novos modelos com tipos acima.
- `not_null` em todas as chaves e métricas; `unique` combinado no grão (`dbt_utils.unique_combination_of_columns` se o pacote já estiver em `packages.yml`, senão teste singular).
- `accepted_values` em `attribution_source`: `['otel_attr','service_map','actor_map','unassigned']`.
- `relationships` (seed → valores de time válidos), `unique` em `service_name`/`actor_id` dos seeds.
- **Reconciliação (singular, exata em micros)** `test_chargeback_equals_daily.sql`: retorna linhas quando
  `sum(cost_usd_micros)` por `day_utc` em `fct_cost_chargeback_daily` ≠ `sum(cost_usd_micros)` por `day_utc` em `fct_cost_daily` (FULL OUTER JOIN por dia; dia ausente de um lado = falha). Idem para `requests`. Mesmo teste para `fct_cost_chargeback_actor_daily` vs `fct_cost_actor_daily` nos dias dentro do TTL.
- **Sem `actor_map` no permanente (singular)** `test_chargeback_daily_no_actor_map.sql`: retorna linhas se existir `attribution_source='actor_map'` em `fct_cost_chargeback_daily`; executado com fixture que tem `actor_map` populado (pseudônimo de teste), provando que o custo foi para `unassigned` e que a reconciliação com `fct_cost_daily` continua exata.
- **Versão HMAC (singular, seed)** `test_chargeback_actor_map_hmac_version.sql`: linhas com prefixo ≠ `hmac:v1:`; severidade warn/error conforme §2.
- **PII (singular)** `test_chargeback_no_pii.sql`: nenhuma coluna String dos novos modelos/seeds contém `'@'`; todo `actor_id` casa `^hmac:v1:[0-9a-f]{64}$` (`match()` no ClickHouse).
- **Estrutura/TTL**: estender guardrail 5 (Alicerce) para cobrir `fct_cost_chargeback*` — já é dinâmico por prefixo `fct_cost*`; prova via `system.tables`.
- **Zero testes pulados**: `dbt build` deve terminar com `SKIP=0` e `ERROR=0`.

## 5. Campos PII
| Campo | Classe | Onde pode existir |
|---|---|---|
| `actor_id` (`hmac:v1:`) | pseudônimo (dado pessoal) | `int_cost_attributed`, `fct_cost_chargeback_actor_daily` (TTL 90d), `chargeback_actor_map` |
| `user.email` em claro | PII direta | **nunca** após o collector; proibido em seeds/modelos |
| `session_id` | quase-identificador | não propagado para os novos fatos |
| `team`, `project`, `owner_team`, `service_name` | não-PII (por política: nomes de time, não de pessoas) | qualquer lugar |

## 6. Critérios de aceite (testáveis)
1. `dbt build` com SKIP=0, ERROR=0, todos os testes novos e antigos PASS.
2. Fixture de #45 (682.950 micros pós-dedup): `sum(cost_usd_micros)` em `fct_cost_chargeback_daily` = 682.950; por dia igual a `fct_cost_daily`.
3. Com seed `chargeback_service_map` vazio: 100% do custo em `team='unassigned'`, total igual.
4. Com `claude-code` mapeado: custo dessa service vai ao time; resto em `unassigned`; total igual.
5. `system.tables`: `fct_cost_chargeback_daily` sem `TTL` em `engine_full`; `fct_cost_chargeback_actor_daily` com `TTL day_utc + toIntervalDay(90)`.
6. `make check-cost-retention` PASS.
7. Nenhum `'@'` em qualquer coluna; regex de `actor_id` 100%.
8. Idempotência: dois `dbt build` seguidos sobre os mesmos dados → mesmos totais.
9. `git diff` não altera nenhum arquivo existente (fase 1).
10. Com `chargeback_actor_map` populado na fixture: zero linhas `attribution_source='actor_map'` em `fct_cost_chargeback_daily`; esse custo aparece em `unassigned` no permanente e com o time mapeado em `fct_cost_chargeback_actor_daily`; reconciliação por dia exata em micros vs `fct_cost_daily`.
11. Seed com linha `hmac:v2:…` (fixture): teste de versão dispara (warn; error em modo estrito) e `audit_cost_runs.actor_map_hmac_version_mismatch_rows` > 0.
12. `audit_cost_runs` registra `unassigned_share_by_source` por dia em todo run.
13. Self-hmac (Duto): testes provam (a) vetor estilo RFC 4231 (HMAC-SHA256 com chave/mensagem conhecidas → hex esperado) e saída exatamente `hmac:v1:<64hex>`; (b) chave ausente ou curta → exit ≠ 0 e stdout/stderr vazios; (c) e-mail e chave não aparecem em stdout além do hash, stderr, logs ou histórico do shell (entrada só por stdin/env).
14. Fase 2 (Duto; aprovada 2026-10-21): os 4 testes de §3 PASS.
15. Self-hmac: documentação do comando declara que só o Vinícius e os administradores do collector podem executá-lo; nenhuma chave (`OTERU_PII_HMAC_KEY`) no repo nem em CI (gitleaks PASS).

## 7. Provas esperadas (em `docs/intents/proof/`, sufixo = SHA curto do commit testado)
- `dbt-build-46-<sha>.txt` (saída completa, linha final com `PASS=… WARN=0 ERROR=0 SKIP=0`).
- `reconciliation-46-<sha>.txt`: `SELECT day_utc, sum(cost_usd_micros) FROM fct_cost_chargeback_daily GROUP BY 1` vs mesmo em `fct_cost_daily`, e diff = 0.
- `system-tables-46-<sha>.txt`: `SELECT name, engine_full, create_table_query FROM system.tables WHERE database='otel' AND name LIKE 'fct_cost_chargeback%'`.
- `pii-46-<sha>.txt`, `check-cost-retention-46-<sha>.txt`, `idempotency-46-<sha>.txt`.

## 8. Open questions para Duto (e outros)
Resolvidas nesta revisão: convenção/transporte de `oteru.team`/`oteru.project` e política de privacidade (§3 Fase 2); obtenção do próprio `hmac:v1:` (ferramenta self-hmac, §2); `actor_map` no permanente (proibido, §2).

Resolvidas pelo Vinícius em 2026-10-21:
- ~~OK para colunas aditivas em `int_api_request_dedup` na fase 2?~~ **Aprovado** (fase 2 aprovada).
- ~~Quem pode executar a ferramenta self-hmac?~~ **Somente o Vinícius e os administradores do collector.**

Em aberto:

1. **Duto:** incluir também `oteru.repo` no futuro?
2. **Vinícius/Finanças:** `owner` = time (não pessoa) é suficiente? Precisa de histórico de mudanças de mapeamento (SCD) ou o mapeamento atual reaplicado no lookback de 48h basta? (Hoje: mudança de seed só afeta as últimas 48h; dias antigos ficam congelados.)
3. Valor de `service.name` é estável o suficiente (`cursor-hooks`, `claude-code-hooks` etc.) para ser chave?

## Status e decisões (2026-10-21)

- Decisões do Vinícius (2026-10-21): (a) **Fase 2 aprovada**, incluindo a mudança aditiva em `int_api_request_dedup` (novas colunas para `oteru.team`/`oteru.project`); (b) o comando admin self-hmac só pode ser executado **pelo Vinícius e pelos administradores do collector**.
- Status: plano concluído; em construção. Fase 1 → Modelo; fase 2 + comando self-hmac → Duto; regra gitleaks → Alicerce; alertas → Vigia; Sentinela revisa o PR quando a feature estiver pronta.
