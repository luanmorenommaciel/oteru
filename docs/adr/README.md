# Architecture Decision Records

Os ADRs registram decisões arquiteturais do Oteru. O status oficial é o declarado no arquivo e ratificado no PR correspondente.

## Índice

| ADR | Tema | Issue | Status | Bloqueio atual |
| --- | --- | --- | --- | --- |
| [ADR-001](./0001-otlp-mcp-minimum-surface.md) | Superfície mínima OTLP/MCP | [#7](https://github.com/luanmorenommaciel/oteru/issues/7) | Proposed | Revisores e aceite do Captain pendentes |
| [ADR-002](./0002-persistent-buffer.md) | Buffer persistente para compliance | [#8](https://github.com/luanmorenommaciel/oteru/issues/8) | Proposed | Revisores e aceite do Captain pendentes |
| [ADR-003](./0003-pii-strategy.md) | Estratégia de PII | [#9](https://github.com/luanmorenommaciel/oteru/issues/9) | Proposed | Revisores e aceite do Captain pendentes |
| [ADR-004](./0004-policy-enforcement.md) | Warn, enforce e Break Glass | [#10](https://github.com/luanmorenommaciel/oteru/issues/10) | Proposed | Revisores e aceite do Captain pendentes |

## Ciclo de vida

- `proposed`: proposta pronta para revisão, sem autoridade para orientar implementação incompatível.
- `accepted`: decisão ratificada pelos papéis definidos em [governança](../governance/README.md).
- `superseded`: substituída por outro ADR, com link bidirecional.
- `rejected`: alternativa avaliada e recusada, preservada para evitar repetição da discussão.

## Regras

1. Copie o [template](./0000-template.md) e preserve o número vinculado à issue.
2. Inclua alternativas reais, consequências, migração e evidência verificável.
3. Diferencie comportamento existente de comportamento decidido para implementação.
4. Não altere silenciosamente um ADR aceito; crie outro e marque a substituição.
5. A implementação deve citar o ADR no PR e testar as invariantes aplicáveis.

