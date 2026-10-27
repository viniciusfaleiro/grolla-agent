## MODIFIED Requirements

### Requirement: Métricas do agente por período
A api SHALL servir `/metricas` com, por semana ou por mês (escolha na página, padrão mês) e para os últimos 6 períodos:
- incidentes resolvidos;
- post mortems aprovados ou encerrados;
- acerto da hipótese principal (certas sobre as avaliadas);
- mediana dos minutos até a hipótese certa;
- "já vi isso" confirmados e recusados;
- vezes que a memória ajudou.

Todas as métricas SHALL sair das respostas humanas e do log, sem LLM. Período sem dado SHALL mostrar "—".

No topo, a página SHALL destacar o período mais recente (incidentes resolvidos, acerto da hipótese principal, "já vi isso" confirmados e post mortems aprovados), com o contexto de cada número; sem avaliação no período, o acerto SHALL dizer quantos post mortems faltam avaliar, com o link para eles. A página SHALL mostrar um gráfico dos incidentes resolvidos nos 6 períodos.

#### Scenario: Mês com avaliações
- **WHEN** no mês houve 4 post mortems avaliados, 3 com a hipótese principal certa
- **THEN** a linha do mês mostra 75% de acerto e a mediana dos minutos informados

#### Scenario: Acerto sem avaliações
- **WHEN** o mês atual tem 2 post mortems sem avaliação do agente e nenhum avaliado
- **THEN** o acerto aparece como "—" com "avaliar 2 post mortems"
