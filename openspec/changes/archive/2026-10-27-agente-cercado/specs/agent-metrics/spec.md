## MODIFIED Requirements

### Requirement: Métricas do agente por período
A api SHALL servir `/metricas` com, por semana ou por mês (escolha na página, padrão mês) e para os últimos 6 períodos:
- incidentes resolvidos;
- post mortems aprovados ou encerrados;
- acerto da hipótese principal (certas sobre as avaliadas);
- mediana dos minutos até a hipótese certa;
- "já vi isso" confirmados e recusados;
- vezes que a memória ajudou;
- pedidos recusados pelo agente (fora do escopo ou pedido de execução, no chat e no console).

Todas as métricas SHALL sair das respostas humanas e do log, sem LLM. Período sem dado SHALL mostrar "—".

#### Scenario: Mês com avaliações
- **WHEN** no mês houve 4 post mortems avaliados, 3 com a hipótese principal certa
- **THEN** a linha do mês mostra 75% de acerto e a mediana dos minutos informados

#### Scenario: Mês com recusas
- **WHEN** no mês o agente recusou 2 pedidos fora do escopo e 1 pedido de execução
- **THEN** a linha do mês mostra 3 pedidos recusados
