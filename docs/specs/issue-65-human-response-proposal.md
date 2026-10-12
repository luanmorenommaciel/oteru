# #65 — decisão humana proporcional à consequência

Revisão: 2026-10-12. Status: plano para revisão; não implementado.
O commit deste documento não autoriza implementação ou enforcement real.

Relacionado ao [plano integrado de cinco dias](operational-risk-5-day-plan.md)
e à [issue #65](https://github.com/luanmorenommaciel/oteru/issues/65).

## 1. Problema e evidência

O sandbox descrito no [README](../../README.md) recebe e exporta telemetria.
A [configuração básica do collector](../../oteru-collector/oteru-collector-config.yml)
liga receivers a exporters; não intercepta a execução de ações de agentes.
Observar uma ação e emitir `deny` depois dela não impede sua execução.

A descrição da #65 pede um gate humano proporcional à consequência, mas não
define contrato, ponto de interceptação, aprovador ou disponibilidade esperada.
Não há medições de carga, tempo de resposta humana ou eficácia de contenção que
justifiquem um SLA. Os valores da demonstração não são parâmetros de produção.

Decidir por evento isolado também é insuficiente: inventário proibido e anomalia
baixa podem produzir respostas conflitantes sobre a mesma ação. Ausência de alerta
não comprova execução bem-sucedida de todas as verificações necessárias.

## 2. Resultado esperado e fronteira de efeito

Demonstrar dois fluxos explicitamente distintos:

1. **Autorização prévia simulada:** pedido sintético de ação → verificações
   completas → avaliação consolidada → revisão quando necessária → executor falso.
2. **Observação posterior:** evento de risco → caso de resposta → triagem humana.
   Não concede autorização, não cancela retroativamente a ação e não alega prevenção.

Toda decisão inclui `enforcement=simulated`, `identity_assurance=fixture_only`
e `action_execution=not_controlled`. A CLI/relatório mostra
“SIMULAÇÃO — não controla execução real”. Somente o executor falso registra
`would_execute`; esse registro não representa efeito externo.

O artefato de decisão não é token de autorização utilizável em produção. Não há
credenciais de execução, chamadas reais, notificações externas ou integração IdP.

## 3. Contratos propostos

Os contratos são compartilhados com #63/#64/#66 e precisam de revisão conjunta.
Usar versão explícita, timestamps UTC, IDs opacos e dados redigidos. São propostas
de schema, não APIs já disponíveis no repositório.

### `action_request/v1`: intenção anterior à execução

| Campo | Contrato |
|---|---|
| `request_id`, `action_id`, `revision` | IDs estáveis e revisão imutável do pedido |
| `requester_subject` | Identidade placeholder do solicitante |
| `operation`, `resource_ref`, `environment` | Operação pretendida, recurso opaco e ambiente |
| `parameters` | Parâmetros normalizados não sensíveis da fixture |
| `scope`, `reversibility`, `data_sensitivity`, `privilege_change` | Contexto estruturado para calcular consequência; `unknown` é explícito |
| `created_at`, `context_version` | Instante e versão do contexto avaliado |
| `provenance` | Somente `fixture` aceito pelo protótipo |

O consumidor calcula `action_digest` sobre JSON canônico contendo todos os
campos materiais da ação e solicitante. Fixture usa valores completos e seguros;
digest de parâmetros mascarados não é suficiente para vincular uma ação real.
Canonicidade e proteção de parâmetros reais são decisões obrigatórias antes de produção.

Mudança em operação, parâmetros, recurso, ambiente ou contexto cria nova revisão
e invalida aprovação pendente/anterior. IDs e digests são referências de integridade
no simulador; não oferecem autenticidade por si sós.

### `operational_risk_event/v1`: observação e evidência

Campos obrigatórios: `event_id`, `source`, `risk_type`, `occurred_at`,
`observed_at`, `severity`, `rule_id`, `rule_version`, `evidence` e `provenance`.
Tipos iniciais: `permission_escalation`, `inventory`, `anomaly`.
Severidade: `low|medium|high|critical|unknown`. Evidência contém fatos mínimos
redigidos e referências locais; não executar instruções nem buscar URLs fornecidas.

`action_id`, revisão/digest e `correlation_id` identificam a relação quando ela é
conhecida. Evento sem vínculo verificável segue para observação posterior;
não é anexado a uma autorização apenas por proximidade temporal ou sessão.
Consequência e decisão sugeridas pela origem são informativas, não autoritativas.

### `check_result/v1`: comprovação de cobertura

Cada verificação obrigatória definida na política publica um resultado explícito:
`check_id`, `check_version`, `request_id`, `action_digest`, `context_version`,
`status=pass|fail|unknown|error`, `completed_at`, `valid_until`, evidências e
IDs de eventos associados. `pass` pode conter zero alertas, mas precisa existir.

O coletor de resultados é um adaptador local, não o OTel Collector. Ele fecha
um snapshot por pedido somente quando todas as verificações obrigatórias têm
resultado, ou quando o cenário fixture manda avaliar com cobertura incompleta.
Resultados ausentes, expirados, com erro ou de outra revisão não contam como
cobertura completa. `fail` recebe o efeito definido por uma regra aplicável;
não significa automaticamente proibição de qualquer ação.

### `human_gate_decision/v1` e `response_case/v1`

A decisão contém `decision_id`, pedido/revisão/digest, `policy_version`,
`snapshot_id`, `required_checks`, `coverage_complete`, consequência calculada,
regras aplicáveis, evidências, `initial_outcome=allow|review|deny`, estado atual,
instantes, prazo de validade quando aplicável e os marcadores de simulação.
O snapshot é imutável e inclui versões, resultados e evidências usados.

O caso posterior contém `case_id`, eventos, motivo, responsável placeholder
quando atribuído, `state=open|acknowledged|resolved|expired_unresolved` e histórico.
Não possui `allow` nem gera permissão; resolver o caso exige justificativa e
evidência e não comprova que uma ação foi bloqueada.

Versão desconhecida, ID ausente, tipo inválido ou payload malformado gera
`input_rejected` e zero autorizações. Informação explicitamente desconhecida
é válida e segue a política. Limites de demo: 64 KiB por entrada e 20 referências
de evidência; exceder gera erro, sem truncamento silencioso.

## 4. Política de consequência e decisão consolidada

A política calcula consequência a partir da operação, ambiente, recurso,
alcance, reversibilidade, sensibilidade e mudança de privilégio. Não aceitar
`consequence=low` declarado pelo detector como autorização. Sem catálogo
aplicável ou contexto suficiente, consequência é `unknown`.

Catálogo mínimo de demo: leitura de fixture pública em sandbox → `low`;
alteração reversível e limitada de fixture → `moderate`; exclusão irreversível,
publicação externa ou ampliação de privilégio → `high`. São exemplos sintéticos;
não equivalem a classificação validada para recursos reais.

### Precedência de `human-response-policy/v2`

| Ordem | Condição aplicável à ação avaliada | Resultado |
|---|---|---|
| 1 | Existe proibição explícita, por exemplo componente negado em política para aquela operação | `deny` |
| 2 | Cobertura incompleta, resultado vencido, erro, regra não suportada ou consequência desconhecida | `review`, sem possibilidade de aprovar enquanto faltar cobertura/contexto |
| 3 | Cobertura completa, consequência moderada/alta ou alguma regra exige julgamento humano | `review`, elegível para aprovação com identidade e escopo válidos |
| 4 | Cobertura completa, consequência baixa e todas as condições permissivas satisfeitas | `allow` |

Proibição prevalece mesmo quando outros resultados são permissivos. Não usar
média de risco, votação ou a última mensagem recebida. Severidade crítica é
evidência: precisa de regra que determine efeito e escopo da ação. Não impede
automaticamente uma ação de recuperação distinta. Sem regra suportada, revisar.
Proibições não são sobrepostas por um clique de aprovação; exceções ficam fora do recorte.

Uma decisão inicial por pedido/revisão/snapshot/política. Mesmo ID e conteúdo
repetidos são idempotentes; mesmo ID com conteúdo diferente é conflito auditável.
Novo risco relevante ou mudança de política/contexto invalida a decisão anterior
antes de uma nova avaliação. Não fundir conjuntos de evidência mutáveis em uma
decisão já aprovada. O executor falso testa essa invalidação antes de `would_execute`.

## 5. Identidade, estados e tempo

Roster fixture versionado define `reviewer_subject`, papéis e escopos.
Usar identidades como `reviewer-demo-01`; não usar nome ou e-mail pessoal.
A resposta registra decisão, snapshot/digest/política, aprovador, razão não vazia
e timestamp. Rejeitar autoaprovação, identidade ausente/desconhecida, papel sem
escopo e aprovação vinculada a outra revisão. Isso testa regras de autoridade,
não autentica uma pessoa real.

| Entrada/transição | Estado | Elegível ao executor falso? |
|---|---|---|
| Política `allow` | `allowed_simulated` | Sim, enquanto contexto e validade permanecerem válidos |
| Política `deny` | `denied_simulated` | Não |
| Política `review` | `pending_review` | Não |
| Aprovação válida, cobertura completa e prazo aberto | `approved_simulated` | Sim, após revalidação |
| Rejeição humana válida | `rejected_simulated` | Não |
| Prazo alcançado sem decisão válida | `expired_simulated` | Não; não é rejeição humana |
| Contexto/política/evidência material mudou | `invalidated_simulated` | Não; exige nova avaliação |

Para demo, usar relógio injetado e `review_window_seconds=300`. Em
`now >= expires_at`, expiração vence. Aprovar não estende a validade: nesta demo
decisões permissivas também expiram aos 300 s da avaliação, ou antes se qualquer
resultado obrigatório vencer. O executor falso revalida essas condições e
consome uma decisão no máximo uma vez por run. Não há esperas reais ou jobs.

Ausência antes do prazo mantém pendência. Expiração deixa autorização ausente,
sem converter silêncio em consentimento nem registrar um humano como autor.
No fluxo posterior, prazo de triagem é outro parâmetro fixture, independente;
vencimento produz `expired_unresolved`, nunca `deny` retroativo ou caso resolvido.
Escalonamento real e prazos operacionais dependem de operação.

Processamento local sequencial: primeira resposta terminal válida vence;
duplicata idêntica é idempotente; conflito ou resposta tardia é registrado sem
alterar o resultado. Invalidação/expiração de validade pode retirar elegibilidade
de uma aprovação, preservando a resposta humana original no histórico.

## 6. Arquitetura e auditoria

```text
ação sintética → plano de verificações → snapshot de resultados + riscos
                                          ↓
                                política pura consolidada
                                          ↓
                               revisão humana simulada
                                          ↓
                             revalidação → executor falso

eventos posteriores → caso de resposta → triagem / resolução documentada

ambos → auditoria local redigida + relatório com marcadores de simulação
```

Começar com funções puras, estado em memória e adaptadores de arquivo explícito.
Não criar serviço distribuído, processor Go, broker ou banco apenas para a demo.
O collector continua exportando telemetria, independentemente da decisão do gate.
Eventos de auditoria não voltam como novos eventos de risco.

Auditoria JSONL local em diretório ignorado: `run_id`, sequência, tipo de evento,
IDs, estado anterior/novo, ator (`policy|human_fixture|clock`), razão, versões,
snapshot/digest, referências redigidas, tempo do cenário e tempo do registro.
Registrar recebimento, avaliação, tentativas inválidas, transições, invalidação,
expiração, conflitos e intenção do executor falso. Publicar transição concluída
somente depois de escrita bem-sucedida; falha de escrita aborta com erro explícito.

Esse arquivo não garante resistência à adulteração nem atomicidade em crash.
Não há recuperação de pendências ou consumo de decisões entre runs; cada run
começa isolado. Manifesto registra versões e fixtures para reprodução. Nunca
persistir captura bruta, parâmetros sensíveis ou segredo; referências opacas
não autorizam buscar evidência não redigida.

### Fronteira futura de enforcement real

Requer interceptador antes da ação, fonte confiável de contexto/checks, IdP e
autorização por escopo, decisão durável vinculada a parâmetros reais, proteção
contra bypass/replay, revalidação e execução coordenadas, consumo único e
confirmação auditável. TOCTOU entre aprovação e execução exige protocolo do
executor; a checagem sequencial da demo não prova sua solução em produção.
Auditoria e estado transacionais, recuperação, revogação, ACL, retenção e política
de indisponibilidade precisam de desenho próprio antes de operação real.

## 7. Plano de cinco dias após validação para implementar

| Dia | Entrega da #65 | Evidência esperada |
|---|---|---|
| 1 | Contratos de ação, risco, cobertura, decisão e caso; catálogo fixture e testes vermelhos | Ausência do mecanismo faz falhar os três cenários canônicos |
| 2 | Adaptadores fixture, validação, separação pré/pós e proteção de evidência | Entradas inválidas rejeitadas e nenhum alerta posterior autoriza ação |
| 3 | Política pura e consolidação com #63/#66 | Proibição prevalece; cobertura ausente não permite aprovação |
| 4 | #64, identidade, relógio, estados e executor falso | Invalidação, expiração e consumo único demonstrados |
| 5 | Integração local, auditoria, CI e roteiro | DoD reproduzido por outra pessoa com resultados explícitos |

O plano integrado organiza as demais frentes. Fixture substituta deve ser marcada
como tal e não prova integração com produtor real. Se o contrato comum não
estiver pronto, registrar DoD de integração pendente; não ampliar prazo/scope
silenciosamente nem declarar a feature operacional concluída.

## 8. Riscos, alternativas e decisões em aberto

| Risco | Tratamento no recorte |
|---|---|
| Confundir recomendação com bloqueio | Separar pré/pós; marcadores em toda saída; zero efeitos reais |
| Zero alertas interpretado como cobertura | Resultados positivos explícitos das verificações obrigatórias |
| Consequência subestimada pela origem | Catálogo na política e contexto estruturado |
| Aprovar enquanto chegam riscos novos | Snapshot imutável e invalidação; sem garantia distribuída |
| Humano aprovar sem evidência suficiente | Cobertura incompleta impede aprovação |
| Auditoria expor dados ou sumir | Evidência redigida, canários e falha de escrita explícita |
| Flood de reviews | Contagens por motivo na demo; capacidade humana ainda não medida |

Descartado: decisão por alerta isolado, tabela somente de severidade, timeout
permissivo, LLM como motor de política, exceção humana sobre proibição e infraestrutura
de produção dentro do timebox. A matriz cartesiana anterior de 80 combinações
não é critério suficiente: deixa de representar a política por ação e cobertura.

- ⚠️ EM ABERTO — catálogo, regras por operação e verificações obrigatórias · decide:
  produto/segurança · trava: semântica final, além do catálogo sintético.
- ⚠️ EM ABERTO — schema, correlação, validade e integridade dos resultados · decide:
  responsáveis por #63/#64/#66 · trava: integração real entre frentes.
- ⚠️ EM ABERTO — roster, separação de funções, prazos e escalonamento · decide:
  operação · trava: atendimento humano real; 300 s continua valor de teste.
- ⚠️ EM ABERTO — onde interceptar, autorização durável, TOCTOU e recuperação · decide:
  arquitetura/operação · trava: enforcement real.
- ⚠️ EM ABERTO — retenção, acesso e integridade de auditoria · decide:
  segurança/operação · trava: produção.

## 9. Definition of Done verificável

Requisitos da implementação futura; não são resultados já executados. Cada grupo
usa run isolado, política e relógio fixos. Preservar evidência de teste vermelho
antes do mecanismo, verde depois e falha ao remover a proteção pertinente.

| Grupo | Cenário e resultado esperado |
|---|---|
| Canônico | 3 ações: leitura segura completa → allow; alteração moderada completa → review; operação proibida → deny. Exatamente 1 decisão inicial de cada tipo |
| Consolidação | 2 resultados conflitantes para a mesma ação, um proibitivo: exatamente 1 deny; inverter ordem não altera resultado |
| Cobertura | Check ausente, vencido, error e unknown: 4 reviews, 0 aprovações aceitas. Pass explícito sem alerta pode satisfazer cobertura |
| Consequência | Mesmo alerta low sobre leitura segura e exclusão irreversível, com cobertura completa: allow e review respectivamente |
| Escopo | Alerta critical sobre operação proibida → deny; ação distinta de recuperação de alto impacto, com checks completos e sem proibição → review |
| Aprovação | Identidade válida aprova/rejeita com motivo e timestamp; ausência, desconhecida, sem escopo, autoaprovação e digest errado → 5 recusas, 0 aprovações |
| Tempo | Sem resposta aos 299 s fica pending; aprovação aos 299 s é válida; aos 300/301 s expira. Executor falso aos 300 s não usa aprovação anterior |
| Invalidação | Alterar parâmetros, política ou evidência relevante após aprovação: 3 cenários, 0 would_execute com decisão antiga |
| Replay | Repetição idêntica gera 1 decisão inicial; mesmo ID alterado gera conflito. Consumo repetido de aprovação válida gera apenas 1 would_execute por run |
| Observação posterior | Evento sem pedido abre 1 caso e 0 autorizações; triagem vencida vira expired_unresolved; não registra bloqueio retroativo |
| Validação | Sem ID, versão desconhecida, enum inválido e payload acima do limite: 4 rejeições e 0 autorizações |
| Auditoria | Falha injetada de escrita: erro explícito, 0 novas transições confirmadas e 0 would_execute decorrentes delas |
| Privacidade | Canário anterior ao scrubbing não aparece em relatório/auditoria: 0 ocorrências. Remover proteção torna o teste vermelho |
| Fronteira | 100% das decisões marcadas simulated e 0 chamadas a executores reais; teste isolado sem adaptador real ou credenciais |

Reconstituir uma decisão pelo snapshot, versões, ator e histórico sem apagar a
recomendação inicial. Documentar cada dependência fixture versus integração real.
Incluir testes do gate nos comandos/CI usados pelo projeto; após implementação,
`make test`, `make lint` e `make pii-guard` devem passar. O guard existente não
substitui o teste de privacidade da auditoria. Outra pessoa deve reproduzir a demo
somente pelo roteiro, sem configuração pessoal ou serviços de produção.
