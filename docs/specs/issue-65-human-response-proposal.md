# #65 — decisão humana proporcional à consequência

Revisão: 2026-10-13. Status: vertical slice local implementado para revisão.
A integração com produtores reais e qualquer enforcement permanecem fora do escopo.

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

Entregar um único fluxo completo da #65: pedido sintético de ação → resultados
explícitos das verificações → avaliação consolidada → revisão quando necessária
→ revalidação → executor falso → auditoria reproduzível.

Eventos observados após a execução, ou sem vínculo verificável ao pedido, geram
`not_authorization_input`, com motivo e zero autorizações. Tratamento de incidentes,
fila de casos, triagem, responsável e escalonamento ficam fora desta issue.
A separação entre observação e autorização é um limite do consumidor; não exige
construir um segundo produto nesta entrega.

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
conhecida. Evento sem vínculo verificável é registrado como não elegível para
autorização; não é anexado apenas por proximidade temporal ou sessão. Além da
correlação, o snapshot exige que a ação ainda esteja pendente no cenário fixture;
telemetria de ação concluída nunca é tratada como pedido de autorização.
Consequência e decisão sugeridas pela origem são informativas, não autoritativas.

### `check_result/v1`: comprovação de cobertura

Cada verificação obrigatória definida na política publica um resultado explícito:
`check_id`, `check_version`, `request_id`, `action_digest`, `context_version`,
`status=pass|fail|unknown|error`, `completed_at`, `valid_until`, evidências e
IDs de eventos associados. `pass` pode conter zero alertas, mas precisa existir.

O adaptador local lê um snapshot fornecido explicitamente por fixture; não
agenda checks nem constrói um serviço de coleta de resultados. A política compara
esse snapshot com a lista versionada de verificações obrigatórias por operação.
Resultados ausentes, expirados, com erro ou de outra revisão não contam como
cobertura completa. `fail` recebe o efeito definido por uma regra aplicável;
não significa automaticamente proibição de qualquer ação.

### `human_gate_decision/v1`

A decisão contém `decision_id`, pedido/revisão/digest, `policy_version`,
`snapshot_id`, `required_checks`, `coverage_complete`, consequência calculada,
regras aplicáveis, evidências, `initial_outcome=allow|review|deny`, estado atual,
instantes, prazo de validade quando aplicável e os marcadores de simulação.
O snapshot é imutável e inclui versões, resultados e evidências usados.

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
| 2 | Cobertura incompleta, resultado vencido, erro, regra não suportada ou consequência desconhecida | `review`, sem possibilidade de aprovar enquanto qualquer condição desta linha persistir |
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
Não há prazo de triagem de incidentes nesta entrega. Prazos de operação real
dependem do responsável pelo atendimento e do futuro executor.

Processamento local sequencial: primeira resposta terminal válida vence;
duplicata idêntica é idempotente; conflito ou resposta tardia é registrado sem
alterar o resultado. Invalidação/expiração de validade pode retirar elegibilidade
de uma aprovação, preservando a resposta humana original no histórico.

## 6. Arquitetura e auditoria

```text
ação sintética + snapshot fixture de verificações e riscos
                                          ↓
                                política pura consolidada
                                          ↓
                               revisão humana simulada
                                          ↓
                             revalidação → executor falso

eventos posteriores/sem vínculo → not_authorization_input

todas as saídas → auditoria local mínima + relatório com marcadores de simulação
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
| 1 | Contratos de ação, risco, cobertura e decisão; catálogo fixture e testes vermelhos | Ausência do mecanismo faz falhar os três cenários canônicos |
| 2 | Adaptador de arquivo fixture, validação e rejeição de entradas posteriores/sem vínculo | Entradas inválidas rejeitadas e nenhum alerta posterior autoriza ação |
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

Critérios do vertical slice; resultados executados são relatados na PR. Cada grupo
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
| Observação posterior | Evento sem pedido e evento de ação já concluída: 2 registros not_authorization_input, 0 autorizações e 0 casos de incidente criados |
| Validação | Sem ID, versão desconhecida, enum inválido e payload acima do limite: 4 rejeições e 0 autorizações |
| Auditoria | Falha injetada de escrita: erro explícito, 0 novas transições confirmadas e 0 would_execute decorrentes delas |
| Privacidade | Canário em campo bruto extra de entrada válida/inválida não é copiado para stdout, stderr ou auditoria: 0 ocorrências. Remover a projeção de saída faz o teste falhar; scrubbing do pipeline é aceite de #67 |
| Fronteira | 100% das decisões marcadas simulated e 0 chamadas a executores reais; teste isolado sem adaptador real ou credenciais |

Reconstituir uma decisão pelo snapshot, versões, ator e histórico sem apagar a
recomendação inicial. Documentar cada dependência fixture versus integração real.
Incluir testes do gate nos comandos/CI usados pelo projeto; após implementação,
`make test`, `make lint` e `make pii-guard` devem passar. O guard existente não
substitui o teste de privacidade da auditoria. Outra pessoa deve reproduzir a demo
somente pelo roteiro, sem configuração pessoal ou serviços de produção.


## 10. Revisão de escopo e preparação para implementação

Esta revisão restringe a responsabilidade da branch à #65. O plano integrado
é contexto de dependências; não autoriza implementar #21/#63/#64/#66/#67 aqui.
Qualidade de engenharia de produção significa contrato preciso, falhas visíveis,
compatibilidade e testes adequados ao efeito prometido. Não transforma uma
simulação sem autenticação/persistência real em controle pronto para produção.

### Decisões e alternativas

| Decisão | Alternativa descartada e motivo |
|---|---|
| Uma única operação fixture parametrizada, com cenários allow/review/deny e falhas | Catálogo genérico de ações: amplia taxonomia antes de validar o mecanismo |
| Consumir snapshot explícito de resultados | Agendar verificações ou montar agregador distribuído: responsabilidade dos produtores, sem necessidade na demo |
| Rejeitar uso de observação posterior como autorização | Máquina de casos, triagem e escalonamento: segundo fluxo de produto fora da #65 |
| Política e máquina de estados coesas, adaptadores mínimos | Framework de plugins, interfaces para múltiplos backends ou motor genérico: abstrações sem consumidor atual |
| Projetar saída por campos seguros e referências opacas | Implementar outro scrubber na #65: duplica #67 e mistura transformação com política |

A operação fixture proposta é alteração de um recurso sintético em sandbox;
parâmetros de alcance/reversibilidade e regras aplicáveis cobrem as três decisões.
Os exemplos de leitura/exclusão na matriz são casos adicionais de política pura,
não compromissos de integração com diferentes executores. Não carregar arquivos,
URLs ou comandos apontados pelos parâmetros da ação: o executor falso apenas
incrementa um contador e emite registro estruturado.

### Fronteiras e falhas visíveis

- Coleta e transporte continuam no emitter/collector. Nenhuma alteração de
  pipeline ou exporter é necessária para rodar a #65 isoladamente.
- Transformação e scrubbing pertencem a #21/#67; detecção a #63/#64/#66.
  O consumidor da #65 valida os resultados e aplica política, sem recriar detectores.
- Auditoria da #65 usa uma projeção com campos estruturados permitidos. Texto
  livre e payload de erro não são copiados; motivos são códigos enumerados e
  referências locais opacas. Se uma justificativa textual for necessária, usa
  somente texto fixture controlado; texto livre de produção permanece fora do recorte.
- Política/roster/schema inválidos abortam antes de qualquer decisão permissiva.
  Não aplicar configuração parcial, defaults permissivos ou fallback de versão.
- `review` e `deny` são resultados válidos da política, não erros de execução.
  CLI proposta: exit 0 para avaliação concluída, exit 2 para entrada/configuração
  inválida, exit 1 para falha operacional, incluindo escrita de auditoria.
  `not_authorization_input` é resultado explícito de roteamento, com exit 0.
- Cada run termina com resumo de entradas aceitas/rejeitadas, decisões por tipo,
  pendências, expirações e would_execute. Sem sucesso silencioso em falha parcial.

### Compatibilidade, manutenção e rollback

Não mudar schemas OTLP existentes, argumentos/defaults do replay ou configurações
padrão do collector. Schema do gate é versionado separadamente; versão não
suportada falha explicitamente, sem migração automática. Antes do código, revisar
com os responsáveis pelos produtores nomes, versões e semântica de check_result;
fixtures locais não impõem unilateralmente uma API às outras issues.

Usar as convenções Python, pytest, Ruff e Make do repositório, sem novo framework.
⚠️ EM ABERTO — localização do módulo de política e integração no ambiente Python ·
decide: responsável pela arquitetura da #65 · trava: primeiro commit de código.
O emitter é gerador de telemetria: não colocar política nele apenas para reutilizar
seu ambiente. Escolher o menor módulo coeso após revisão de layout, sem abrir uma
arquitetura de serviços. Registrar owner de política e de schema antes da integração.

Rollback do recorte: parar a invocação explícita da demo e reverter somente o commit
funcional da #65; conservar auditoria para investigação. Sem migrations, serviço
residente, configuração padrão alterada ou dados de produção para restaurar.
Verificar após a reversão que o replay continua operando via `make dry-run` e que
nenhum comando padrão inicia o gate. Artefatos locais de runs usam diretório
ignorado e distinto; não sobrescrever runs anteriores ou apagar logs no rollback.

### Comandos e aceite executável planejado

O Makefile atual executa `make test` e `make lint` somente no emitter. Eles **não
comprovam cobertura do gate**. Os targets abaixo foram adicionados à branch e devem rodar no CI com os paths
do módulo, testes, fixtures e política.

| Comando | Resultado esperado após implementação |
|---|---|
| `make test-human-gate` (novo) | Exit 0; todos os grupos da seção 9 passam, sem testes skipped/xfail para requisitos obrigatórios |
| `make lint-human-gate` (novo) | Exit 0; zero erros Ruff e zero arquivos fora de formato no módulo/testes novos |
| `make demo-human-gate GATE_OUTPUT=tmp/<novo-run>` | Exit 0; 13 cenários válidos: initial_allow=1, initial_review=11, initial_deny=1, would_execute=2; check inválido rejeitado sem artefato; zero efeitos externos |
| `make test` | Exit 0; suíte existente sem regressão |
| `make lint` | Exit 0; zero erros existentes |
| `make dry-run` | Exit 0; replay valida sample sem envio de rede |
| `make pii-guard` | Exit 0; zero violações nas superfícies que o guard cobre |

Usar Python 3.10/3.13 e sistemas da matriz existente, preservando execução
macOS/Linux e Windows com Git Bash. Não duplicar ambientes CI sem necessidade.
O README da entrega deve documentar bootstrap dos novos targets, paths de saída,
códigos de erro, um exemplo de falha e rollback. O canário de auditoria é teste
separado do guard; a demo não requer Docker nem serviços externos.

O recorte implementado usa o pacote `oteru_gate/` e fixtures locais. Falta
validar com os responsáveis pelos produtores um contrato de `check_result`
autenticado e com validade antes de integrar dados reais. Os testes e o roteiro
da PR são a evidência desta simulação; não provam enforcement operacional.

### Contrato implementado e dependências abertas

O recorte entregue aceita `request` sintético com `request_id`, `action_id`,
`revision`, solicitante, operação, recurso, ambiente, alcance, reversibilidade,
sensibilidade, mudança de privilégio, parâmetros e `created_at`. Cada check
fixture tem `check_id`, `status` e referências de evidência. `evaluate` exige
resultado explícito para `inventory`, `permission` e `anomaly`; um evento prévio
não mapeado é rejeitado em vez de ser ignorado. A recomendação, o digest da ação
e o digest do snapshot aparecem no relatório. A resposta de revisão deve citar
ambos os digests e um aprovador presente no roster fixture para a operação.

O contrato completo de `check_result/v1` descrito acima **ainda não está
implementado**: a fixture não autentica origem, não vincula criptograficamente
cada resultado ao pedido, não carrega versão/validade individual nem prova que
os produtores concluíram a coleta. `expired` é um status explícito da fixture.
Essas lacunas impedem ligar o simulador a dados reais ou afirmar autorização
confiável. A integração exige acordo de schema com #63/#64/#66 e testes de
proveniência, validade e correlação. O digest local detecta mudança entre o
snapshot avaliado e um `current_checks` fornecido na tentativa simulada, mas
não substitui essas garantias.

Não foi criada máquina de casos de incidente. Eventos posteriores são apenas
marcados `not_authorization_input`; o workflow de triagem é outro produto.
Não existe token, interceptador, aprovação autenticada, fila durável ou SLA.
Rollback é retirar a invocação explícita da demo/reverter o commit funcional;
o collector e o emitter continuam com os mesmos defaults.
