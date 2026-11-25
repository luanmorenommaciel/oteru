# ADR-003 — Estratégia configurável de PII

- **Status:** Proposed (revisada após review; implementação é acompanhada separadamente)
- **Data:** 2026-11-02
- **Issue:** [#9](https://github.com/luanmorenommaciel/oteru/issues/9)
- **Decidor:** Vinicius Sena, Captain
- **Revisora de segurança/compliance:** Silmara (`silmarahp`)

## Contexto

O PII guard impede que capturas e fixtures versionadas contenham identidade real; ele não transforma telemetria em runtime. O Collector já aplica uma policy local antes dos exporters cobertos. Hoje há uma policy controlada pelo operador por instalação dedicada, não uma policy selecionável por tenant enviado no OTLP. O contrato implementado está documentado em [`docs/privacy.md`](../privacy.md) e usa policy JSON v2.

Esta ADR decide a direção de produto para a evolução de #21. Ela não altera nem rebatiza silenciosamente o comportamento já entregue em #94/#154: `hash` é HMAC-SHA-256 com chave; `mask` substitui por `REDACTED`; `drop` remove o atributo. O valor HMAC é estável para o mesmo campo e valor enquanto a chave não mudar, portanto é pseudonimização com capacidade de correlação, não anonimização. A implementação atual cobre os exporters declarados no runbook; modos que enviam diretamente ao backend e contornam o Collector não recebem essa proteção.

## Decisão proposta

O contrato-alvo oferece quatro ações por campo:

- `hash`: pseudonimiza via HMAC-SHA-256 quando correlação estável for necessária;
- `mask`: substitui o valor por `REDACTED`;
- `drop`: remove o campo antes de buffer, storage, logs e métricas;
- `encrypt`: cifra o valor com chave gerenciada e controle de acesso separado.

`ignore` não é uma ação de transformação no contrato implementado: é o default seguro para campos sem regra. O schema atual não aceita `ignore` como ação explícita. “Ignore” significa omitir/remover o campo sensível; não se aplica indiscriminadamente ao evento inteiro. Campos operacionais benignos, valores de métricas, timestamps e contexto estrutural são preservados conforme as regras e os filtros descritos no runbook.

Defaults propostos para a evolução:

1. Os seis atributos de identidade catalogados continuam usando `hash` por padrão na implementação atual; mudanças exigem migração explícita de policy e avaliação dos dashboards que dependem da forma pseudônima.
2. Conteúdo conhecido de prompt, entrada/saída de ferramentas e resposta é removido nos contextos cobertos. Texto livre do corpo de logs é preservado por padrão e pode conter dados pessoais; a policy atual permite substituir o corpo inteiro por `REDACTED`.
3. `encrypt` só poderá ser ativado com chave válida resolvida por uma integração de gerenciamento de chaves aprovada, autorização separada para decrypt e justificativa/retention configurada. A ADR ainda não escolhe provedor nem formato de referência da chave: isso é bloqueio de implementação, não uma licença para inventar um backend local ou persistir chave no arquivo da policy.
4. Campo sensível sem regra aplicável não pode ser exportado em claro. Falha de validação, chave ausente ou erro de cifra deve interromper o caminho afetado sem fallback em claro; quarentena só poderá ser usada quando seu armazenamento também estiver protegido e explicitamente configurado.

## Schema implementado hoje

O exemplo abaixo descreve semântica, não o schema literal do Collector. A policy executável atual está em `oteru-collector/privacy/policy.json`, usa `version: 2`, `identity`, `content` e `metrics`, e aceita `hash`, `mask` e `drop` conforme `docs/privacy.md`. Ela não implementa `encrypt`, policy multi-tenant, nem os campos `tenant_id`, `rules` ou `retention_days` deste rascunho anterior.

## Contrato-alvo para a próxima versão da policy

O schema final precisa ser versionado separadamente e validar ações, escopos, tipos e referências de chave antes da ativação. Este exemplo é ilustrativo e **não é aceito pelo compilador atual**:

```yaml
version: 3
installation_id: example-installation
default_action: ignore
rules:
  - field: user.email
    action: hash
    method: hmac-sha256
    key_ref: pii-linkability-v1
  - field: user.id
    action: hash
    method: hmac-sha256
    key_ref: pii-linkability-v1
  - field: prompt.content
    action: drop
  - field: approved.audit.subject
    action: encrypt
    key_ref: pii-encryption-v3
    purpose: approved-audit-retention
```

Políticas inválidas são rejeitadas antes de ativação. Metadados operacionais podem registrar versão da policy, ação e identificador não secreto da chave; nunca valor original, ciphertext sensível em log, nem material de chave. O contrato de retenção deve referenciar o controle operacional que realmente expira dados no backend; um número na policy, por si só, não implementa retenção.

## Matriz inicial

| Classe de dado | Regra atual / proposta | Observação |
| --- | --- | --- |
| E-mail, conta e identidade pessoal | Hoje: `hash` HMAC nos seis campos catalogados; alvo pode permitir `mask`, `drop` ou `encrypt` explícitos. | A chave atual é da instalação. A ADR não afirma suporte por tenant. |
| `session.id` e `prompt.id` | Preservados hoje. | São atributos de correlação com risco de linkabilidade; não são os IDs estruturais OTLP. Uma policy futura poderá transformá-los apenas com regra que mantenha joins coerentes. |
| `trace_id` e `span_id` OTLP | Preservados hoje; não são atributos comuns da policy. | A policy não deve reescrevê-los como strings arbitrárias. Preservar mantém a estrutura da trace; remover ou remapear exige transformação consciente de todos os links, parent IDs, events e exemplars. V1 não oferece remapeamento. |
| Prompt, tool input/output e resposta | Campos de conteúdo conhecidos são removidos nos contextos cobertos; conteúdo livre de log é preservado, salvo `content.log_body: drop`. | Não afirmar sanitização de texto livre; encrypt permanece não implementado. |
| Organização/tenant | Policy de instalação; `tenant.id` enviado pelo cliente não seleciona policy. | Compartilhamento multi-tenant de receiver está fora do comportamento implementado. |
| Métricas e dimensões | Métricas/timestamps e dimensões benignas preservados; identidade catalogada recebe HMAC. Dimensões incompatíveis com `mask`/`drop`, conteúdo conhecido ou exemplars são rejeitados pelos filtros de segurança. | Não remover silenciosamente dimensões que alterariam a identidade da série. |

## Pseudonimização e chaves

- Hash simples sem chave não é aceito para PII de baixa entropia.
- HMAC atual usa um segredo operacional da instalação e separação de domínio por nome de campo; não há chave por tenant/finalidade. Não reutilizar ou descrever essa chave como chave de encryption.
- Encryption futura precisa definir formato autenticado, nonce, identificador de chave, encoding e limites de tamanho. Algoritmo e envelope ainda dependem da integração de chaves escolhida.
- Rotação HMAC altera os pseudônimos e interrompe continuidade entre versões de chave. Migração/dual-read ainda não está implementada nem aprovada; nunca se deve recuperar plaintext para recalcular sem uma fonte autorizada.
- Acesso a decrypt deve ser separado do acesso comum à observabilidade e auditado. Identidades autorizadas, fluxo de aprovação e comportamento de emergência ainda precisam ser especificados antes de habilitar Encrypt.

## Threat model resumido

| Ameaça | Controle |
| --- | --- |
| Reidentificação por dicionário | HMAC com segredo fora do payload; hoje o escopo da chave é a instalação |
| Vazamento em log de erro | Logs estruturados sem payload e testes de redaction |
| Chave ausente ou expirada | Para HMAC, o Collector atual não inicia sem chave válida. Para Encrypt futuro, interromper sem fallback; quarentena depende de armazenamento protegido configurado |
| Cross-tenant | Não há isolamento multi-tenant implementado; deployments com dados de tenants distintos precisam de instalações isoladas até que identidade e seleção de policy confiáveis sejam projetadas |
| Reprocessamento com policy antiga | `policy_version` e migração explícita |
| Operador com acesso amplo | Menor privilégio e auditoria de decrypt/replay |

## Alternativas consideradas

### SHA-256 sem chave

Rejeitada para PII de baixa entropia por permitir ataques de dicionário e correlação entre instalações.

### Uma ação global para todos os campos

Rejeitada porque finalidade, risco e obrigação de retenção variam por classe de dado.

### Criptografar tudo

Rejeitada como default por ampliar custo, superfície de acesso e risco de uso indevido. Minimização precede retenção.

## Migração e rollback

1. Inventariar campos, exporters e rotas de ingestão; a configuração direta de ClickStack contorna o Collector e permanece fora deste controle.
2. Evoluir o contrato de policy em versão explícita, mantendo compatibilidade e migração documentadas; não chamar a policy atual de schema v1.
3. Validar policy e disponibilidade das chaves antes de aceitar tráfego; aplicar transformação antes de buffer/exporter e testar todos os sinais e destinos habilitados.
4. Definir migração e retenção dos dados históricos com o backend e base legal; trocar a policy não altera dados já persistidos.
5. Rollback nunca deve reintroduzir plaintext. Se não houver uma policy segura ou chave válida, parar ingestão ou remover o campo de forma segura.

## Evidências para ratificação

- [x] Validador do contrato local atual e testes de ausência de valores catalogados nos destinos cobertos (ver `docs/privacy.md`).
- [ ] Decidir provedor e escopo da chave de encryption, envelope autenticado, autorização de decrypt, rotação/recuperação e retenção/quarentena protegida.
- [ ] Definir semântica de migração do contrato atual `hash`/`mask`/`drop` para o alvo sem quebrar correlação e dashboards.
- [ ] Definir política confiável para tenancy antes de anunciar policy por tenant.
- [ ] Expandir testes para encryption, logs/erros e cada destino/rota operacional que a V1 venha a suportar.
- [ ] Threat model revisado por segurança/compliance.
- [ ] Aceite do aprovador.
