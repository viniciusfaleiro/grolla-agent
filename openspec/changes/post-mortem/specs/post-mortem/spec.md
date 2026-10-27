## ADDED Requirements

### Requirement: Rascunho ao resolver o incidente
Quando um incidente é resolvido, o pedido do post mortem SHALL ser gravado na mesma transação. O worker SHALL então gerar o rascunho com as seções do template, nesta ordem:
1. resumo executivo;
2. impacto;
3. métricas do incidente;
4. timeline;
5. detecção;
6. fatores contribuintes;
7. hipóteses investigadas;
8. o que funcionou, o que não funcionou e onde tivemos sorte;
9. plano de ação;
10. avaliação do agente.

As métricas (TTD, TTA, TTM e TTR) e a timeline SHALL ser montadas pelo núcleo a partir do incidente e do log de eventos, sem LLM, e cada item da timeline SHALL ter a origem: alerta, agente, chat ou pessoa. As demais seções SHALL ser escritas pelo modelo numa chamada sem ferramentas, com o log de eventos, a análise, a validação, os ciclos e o chat como dado. O texto SHALL ser blameless (fatores contribuintes, sem culpados). O status inicial SHALL ser rascunho.

Incidente descartado SHALL NOT gerar post mortem. Se o modelo falhar, o post mortem SHALL sair com as seções determinísticas e as demais marcadas como "não gerada: <motivo>", e o botão de regenerar resolve.

#### Scenario: Incidente resolvido
- **WHEN** o IC encerra um P1 do `pedidos-api` com o motivo "rollback do build 20261026.1"
- **THEN** em até 1 min existe o post mortem em rascunho, com as 10 seções, TTD/TTA/TTM/TTR calculados e a timeline com a origem de cada item

#### Scenario: Modelo fora do ar
- **WHEN** o modelo falha ao gerar o rascunho
- **THEN** o post mortem existe com métricas e timeline, e as outras seções dizem "não gerada: LLM indisponível"

### Requirement: Edição por seção com versões
Uma pessoa identificada SHALL poder editar qualquer seção. Toda mudança SHALL guardar a versão anterior com o autor (pessoa ou "Grolla"), a data e o motivo, e a página SHALL mostrar o histórico de cada seção. A primeira edição humana de um rascunho SHALL mover o status para em revisão.

#### Scenario: Primeira edição
- **WHEN** alguém edita o resumo executivo de um post mortem em rascunho
- **THEN** a seção muda, a versão do agente fica no histórico e o status passa a em revisão

### Requirement: Regenerar seção
Uma pessoa identificada SHALL poder pedir que o agente regenere uma seção escrita pelo modelo, com uma instrução opcional. O agente SHALL reescrever só essa seção, recebendo o post mortem atual (inclusive as edições humanas) e o log de eventos como dado, e a versão anterior SHALL ficar no histórico. As seções determinísticas SHALL ser recalculadas do log, sem LLM. Post mortem aprovado ou encerrado SHALL NOT ser regenerado.

#### Scenario: Regenerar com instrução
- **WHEN** alguém pede para regenerar "fatores contribuintes" com a instrução "incluir a falta de alarme no RDS"
- **THEN** em até 1 min a seção é reescrita com isso, e a versão anterior está no histórico com o autor "Grolla"

### Requirement: Plano de ação estruturado
O plano de ação SHALL ser uma lista de itens, cada um com descrição, dono, prazo, tipo (`prevenir`, `detectar` ou `mitigar`) e situação (`aberto`, `concluido` ou `aceito`). O rascunho do modelo SHALL propor itens sem dono nem prazo. Uma pessoa identificada SHALL poder adicionar, editar e concluir itens. Item aberto com prazo vencido SHALL aparecer como vencido.

#### Scenario: Item vencido
- **WHEN** um item aberto tem prazo ontem
- **THEN** a página do post mortem e a lista mostram o item como vencido

### Requirement: Ciclo de vida
O status SHALL seguir rascunho → em revisão → aprovado → encerrado, e toda mudança SHALL guardar o autor e a data.
- Aprovar SHALL exigir que todo item do plano tenha dono e prazo.
- Encerrar SHALL exigir que todo item esteja concluído ou aceito.
- Uma pessoa identificada SHALL poder voltar de aprovado para em revisão, para corrigir algo.

#### Scenario: Aprovar sem dono
- **WHEN** alguém tenta aprovar com um item do plano sem dono
- **THEN** a aprovação é recusada com a mensagem nomeando o item

#### Scenario: Encerrar
- **WHEN** todos os itens estão concluídos ou aceitos e alguém encerra o post mortem
- **THEN** o status passa a encerrado com o autor e a data

### Requirement: PDF da versão aprovada
A api SHALL servir o PDF de um post mortem aprovado ou encerrado, com o título do incidente, o status, as métricas, todas as seções na ordem do template e o plano de ação. Post mortem em rascunho ou em revisão SHALL NOT ter PDF.

#### Scenario: Baixar o PDF
- **WHEN** alguém abre o PDF de um post mortem aprovado
- **THEN** recebe um `application/pdf` com o resumo executivo e o plano de ação

### Requirement: Lista de post mortems
A api SHALL servir `/postmortems` com os post mortems, os mais recentes primeiro, filtráveis por status, severidade, serviço e período (incidente detectado entre duas datas). Cada linha SHALL mostrar o título, a severidade, os serviços, o status, a data e o número de itens vencidos, e a lista SHALL destacar os itens vencidos. A página do incidente resolvido SHALL ter o link para o post mortem.

#### Scenario: Filtrar por serviço
- **WHEN** alguém filtra a lista por `pedidos-api` e status em revisão
- **THEN** vê só os post mortems desse serviço nesse status
