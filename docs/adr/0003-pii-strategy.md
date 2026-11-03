# ADR-003 — Estratégia configurável de PII

- **Status:** Proposed
- **Data:** 2026-11-02
- **Issue:** [#9](https://github.com/luanmorenommaciel/oteru/issues/9)
- **Decidor:** Vinicius Sena, Captain
- **Revisores:** a definir na #13

## Contexto

O repositório possui um PII guard para impedir que capturas e fixtures versionadas contenham identidade real. Esse guard não transforma telemetria em runtime. O replay atual preserva atributos de identidade principal para manter fidelidade à captura.

Portanto, “o guard passou” não significa que storage e logs de produção estejam redigidos. A política precisa atuar antes da persistência e ser configurável por tenant e campo.

## Decisão proposta

Cada tenant terá uma policy versionada com três ações:

- `mask`: substitui o valor por token irreversível ou HMAC quando correlação for necessária;
- `ignore`: remove o campo antes de buffer, storage, logs e métricas;
- `encrypt`: cifra o valor com chave gerenciada e controle de acesso separado.

Defaults:

1. identificadores diretos conhecidos usam `mask`;
2. prompt, parâmetros e respostas usam `ignore`, salvo allowlist explícita;
3. `encrypt` exige configuração explícita, chave válida e justificativa de retenção;
4. campo sensível sem regra aplicável não é persistido em claro;
5. falha de chave em ação `encrypt` envia o evento protegido para quarentena ou recusa a ingestão; nunca rebaixa para texto claro.

## Schema de policy v1

```yaml
version: 1
tenant_id: example-tenant
default_action: ignore
rules:
  - field: user.email
    action: mask
    method: hmac-sha256
    key_ref: pii-linkability-v1
  - field: user.id
    action: mask
    method: hmac-sha256
    key_ref: pii-linkability-v1
  - field: prompt.content
    action: ignore
  - field: approved.audit.subject
    action: encrypt
    key_ref: pii-encryption-v3
retention_days: 30
```

Policies inválidas são rejeitadas antes de ativação. O evento registra apenas `policy_version`, ação aplicada e `key_id`; nunca material de chave.

## Matriz inicial

| Classe de dado | Default | Permitido | Observação |
| --- | --- | --- | --- |
| E-mail, conta e identidade pessoal | Mask | Mask, ignore, encrypt | HMAC com chave de tenant quando houver correlação |
| Session/trace/span IDs | Mask/preservar conforme finalidade | Mask, ignore | Não presumir que correlação seja anônima |
| Prompt, tool input/output e resposta | Ignore | Ignore, encrypt | Persistência em claro proibida |
| Organização/tenant | Mask | Mask, encrypt | Segregação não depende do valor declarado no payload |
| Host e caminho de usuário | Ignore | Ignore, mask | Evitar identidade indireta |
| Métricas agregadas sem identificador | Preservar | Preservar | Verificar risco de reidentificação |

## Pseudonimização e chaves

- Hash simples sem chave não é aceito para identificadores de baixa entropia.
- HMAC usa chave separada por tenant e finalidade.
- Ciphertext inclui `key_id`, algoritmo e nonce; chave nunca acompanha o evento.
- Rotação cria nova versão. Uma janela de dual-read permite migração sem manter plaintext.
- Acesso a decrypt é separado de acesso a observabilidade e sempre auditado.

## Threat model resumido

| Ameaça | Controle |
| --- | --- |
| Reidentificação por dicionário | HMAC com chave e segregação por tenant |
| Vazamento em log de erro | Logs estruturados sem payload e testes de redaction |
| Chave ausente ou expirada | Quarentena/recusa; nunca fallback para claro |
| Cross-tenant | Identidade do tenant derivada de credencial, não de campo do cliente |
| Reprocessamento com policy antiga | `policy_version` e migração explícita |
| Operador com acesso amplo | Menor privilégio e auditoria de decrypt/replay |

## Alternativas consideradas

### SHA-256 sem chave

Rejeitada para PII de baixa entropia por permitir ataques de dicionário e correlação entre tenants.

### Uma ação global para todos os campos

Rejeitada porque finalidade, risco e obrigação de retenção variam por classe de dado.

### Criptografar tudo

Rejeitada como default por ampliar custo, superfície de acesso e risco de uso indevido. Minimização precede retenção.

## Migração e rollback

1. Inventariar campos e storages existentes.
2. Ativar policy em shadow mode, reportando violações sem copiar valores.
3. Aplicar transformação antes do buffer persistente.
4. Reprocessar ou expirar dados históricos conforme base legal.
5. Rollback de policy nunca reintroduz plaintext; volta para `ignore` quando não houver ação segura.

## Evidências para ratificação

- [x] Guard de fixtures existente e limite runtime documentado.
- [ ] Validador de schema e policy por tenant.
- [ ] Testes que procuram PII em storage, logs, DLQ e erros.
- [ ] Testes de rotação, chave ausente e segregação.
- [ ] Threat model revisado por segurança/compliance.
- [ ] Aceite do aprovador.

