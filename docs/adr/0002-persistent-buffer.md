# ADR-002 — Buffer persistente para caminhos de compliance

- **Status:** Proposed
- **Data:** 2026-11-02
- **Issue:** [#8](https://github.com/luanmorenommaciel/oteru/issues/8)
- **Implementação relacionada:** [#6](https://github.com/luanmorenommaciel/oteru/issues/6)
- **Decidor:** Vinicius Sena, Captain
- **Revisores:** a definir na #13

## Contexto

O pipeline atual envia lotes ao Collector e pode persistir em arquivo ou encaminhar a backends. O emitter relata falhas por lote e continua; ainda não existe uma fronteira durável que garanta a recuperação de telemetria reconhecida como evidência de compliance.

Aceitar um registro antes de torná-lo durável permite perda silenciosa durante indisponibilidade de rede, processo, Collector ou backend.

## Decisão proposta

Todo caminho marcado como `compliance-grade` terá uma fila persistente desacoplada entre a recepção e os consumidores downstream.

- O produtor recebe ACK somente após persistência durável do envelope.
- A entrega downstream é **at-least-once**.
- Consumidores devem ser idempotentes; exatamente uma vez não será prometido.
- Falha de persistência retorna erro retryable e nunca ACK de sucesso.
- Caminhos não regulados podem usar política distinta, desde que explicitamente configurada e observável.

A tecnologia de fila permanece decisão de implementação da #6, desde que cumpra as invariantes deste ADR.

## Envelope mínimo

| Campo | Finalidade |
| --- | --- |
| `event_id` | Chave estável de idempotência |
| `tenant_id` | Isolamento e roteamento |
| `received_at` | Momento de recepção |
| `signal_type` | Logs, métricas ou traces |
| `content_type` | Codificação do payload |
| `schema_version` | Evolução compatível |
| `payload_digest` | Verificação de integridade |
| `attempt` | Contagem de entrega |
| `policy_class` | Identifica caminho de compliance |
| `key_id` | Chave usada quando o payload estiver criptografado |

## Garantias e limites

- **RPO proposto:** zero para eventos cujo ACK foi devolvido.
- **RTO proposto:** retomada automática em até 30 minutos após restauração da dependência.
- **Ordenação:** preservada apenas por partição/tenant e origem; não há ordem global.
- **Backpressure:** limite de disco e idade geram alerta antes de recusar novas gravações.
- **DLQ:** somente após política de tentativas; o payload continua protegido e reprocessável.
- **Replay:** exige identidade autorizada, motivo, intervalo e trilha de auditoria.
- **Retenção:** definida por tenant/política; expiração deve ser verificável.

RPO e RTO precisam ser ratificados com a capacidade operacional disponível.

## Cenários obrigatórios de falha

1. Collector indisponível antes da persistência: sem ACK; produtor tenta novamente.
2. Backend indisponível depois da persistência: evento permanece pendente.
3. Reinício durante envio: duplicação é possível; perda de evento confirmado não.
4. Disco próximo do limite: alerta e backpressure antes da exaustão.
5. Payload inválido: registro protegido em quarentena/DLQ, sem loop infinito.
6. Tenant ou chave inválida: nenhuma gravação cruzada; erro redigido.

## Observabilidade

Métricas mínimas: profundidade e bytes da fila, idade do evento mais antigo, taxa de entrada/saída, retries, DLQ, rejeições, latência até ACK, espaço livre e divergência do digest.

Logs nunca incluem payload, prompt, credencial ou PII bruta. Alertas distinguem degradação, recusa e risco de RPO.

## Alternativas consideradas

### Apenas retry no produtor

Rejeitada porque o processo pode encerrar e perder estado.

### Arquivo do Collector como evidência final

Rejeitada como garantia única porque rotação e consumo não definem ACK, idempotência, isolamento ou replay governado.

### Exactly-once ponta a ponta

Rejeitada por custo e falsa segurança entre sistemas heterogêneos. At-least-once com idempotência é verificável.

## Segurança e privacidade

Dados são criptografados em repouso, segregados por tenant e acessíveis por menor privilégio. Chaves seguem ADR-003. Replay e leitura de DLQ produzem eventos de auditoria.

## Migração e rollback

1. Implantar em shadow mode, copiando tráfego sem alterar ACK.
2. Validar integridade, duplicação e recuperação sob falhas.
3. Mover o ACK para a fronteira durável por tenant.
4. Rollback volta o roteamento, preservando a fila para reconciliação; nunca descarta backlog.

## Evidências para ratificação

- [x] Lacuna do pipeline atual documentada.
- [ ] Escolha de tecnologia e capacity plan.
- [ ] Testes de crash, indisponibilidade, disco cheio, replay e idempotência.
- [ ] Validação dos alvos RPO/RTO.
- [ ] Revisão de segurança/compliance.
- [ ] Aceite do aprovador.

