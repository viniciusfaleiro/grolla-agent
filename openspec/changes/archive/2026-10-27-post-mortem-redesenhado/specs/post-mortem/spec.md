## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: Documento mostra o que falta
A página do post mortem SHALL mostrar, no topo, a próxima ação do ciclo de vida em destaque e os números do incidente (duração e TTA, TTM e TTR). Um índice das seções SHALL dizer quem escreveu cada uma por último e o que falta nela: itens do plano sem dono ou prazo e a avaliação do agente pendente. O plano de ação SHALL aparecer em tabela, com ação, tipo, dono, prazo e situação.

#### Scenario: Rascunho com plano sem dono
- **WHEN** alguém abre um rascunho com 3 itens no plano sem dono
- **THEN** a página mostra "Começar revisão" em destaque e o índice marca o plano de ação com "3 sem dono"
