# post-mortem Specification

## Purpose
TBD - created by archiving change post-mortem. Update Purpose after archive.
## Requirements
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
A api SHALL servir `/postmortems` com os post mortems, os mais recentes primeiro, filtráveis por status, severidade, serviço, período (incidente detectado entre duas datas), só os com itens do plano vencidos e só os sem avaliação do agente. A página SHALL mostrar quantos post mortems os filtros deixaram e um jeito de limpar os filtros.

No topo, a página SHALL mostrar quantos rascunhos esperam revisão, quantos estão em revisão, quantos itens do plano estão vencidos e quantos post mortems foram encerrados no mês.

Cada linha SHALL mostrar o título, a severidade, os serviços, a causa numa frase, o status, a situação do plano (itens sem dono, ou concluídos sobre o total), a data e o número de itens vencidos, e a lista SHALL destacar os itens vencidos. A página do incidente resolvido SHALL ter o link para o post mortem.

#### Scenario: Filtrar por serviço
- **WHEN** alguém filtra a lista por `pedidos-api` e status em revisão
- **THEN** vê só os post mortems desse serviço nesse status

#### Scenario: Só os sem avaliação
- **WHEN** alguém filtra por "sem avaliação do agente"
- **THEN** vê só os post mortems em que o time ainda não avaliou o agente

### Requirement: Campos de avaliação no post mortem
A página do post mortem SHALL mostrar e permitir editar os campos de avaliação do agente (hipótese principal certa, minutos até a hipótese certa e memória ajudou) junto da seção "Avaliação do agente", e o PDF SHALL trazê-los.

#### Scenario: PDF com avaliação
- **WHEN** um post mortem aprovado tem a avaliação preenchida
- **THEN** o PDF mostra "Hipótese principal certa: sim", os minutos e "Memória ajudou: sim"

### Requirement: Itens da transcrição na timeline do post mortem
A timeline do post mortem SHALL incluir os eventos extraídos da transcrição com a origem "transcrição" e o horário dito na call, quando houver, na ordem dos horários.

#### Scenario: Post mortem com a call
- **WHEN** o post mortem de um incidente com transcrição é gerado
- **THEN** a timeline tem "[transcrição]" com a decisão do rollback às 03:12

### Requirement: Documento mostra o que falta
A página do post mortem SHALL mostrar, no topo, a próxima ação do ciclo de vida em destaque e os números do incidente (duração e TTA, TTM e TTR). Um índice das seções SHALL dizer quem escreveu cada uma por último e o que falta nela: itens do plano sem dono ou prazo e a avaliação do agente pendente. O plano de ação SHALL aparecer em tabela, com ação, tipo, dono, prazo e situação.

#### Scenario: Rascunho com plano sem dono
- **WHEN** alguém abre um rascunho com 3 itens no plano sem dono
- **THEN** a página mostra "Começar revisão" em destaque e o índice marca o plano de ação com "3 sem dono"

### Requirement: Comandos aplicados no post mortem
Os comandos que o time confirmou ter aplicado SHALL entrar no post mortem: na timeline, como item de origem pessoa com o horário, o autor, o passo, o comando e onde rodou; e no dado do rascunho, para o modelo citá-los na resposta ao incidente e no plano de ação. O card de encerramento SHALL listá-los em "Ações do time", com o comando e o lugar.

#### Scenario: Rollback aplicado
- **WHEN** "Ana" confirmou às 15:31 o comando `kubectl scale deploy/antifraude-api --replicas=2 -n loja` no "cluster loja-prod, namespace loja" e o incidente é resolvido
- **THEN** a timeline do post mortem tem o item das 15:31, de origem pessoa, com "Ana", o passo, o comando e o lugar, e o card de encerramento lista o mesmo comando em "Ações do time"

