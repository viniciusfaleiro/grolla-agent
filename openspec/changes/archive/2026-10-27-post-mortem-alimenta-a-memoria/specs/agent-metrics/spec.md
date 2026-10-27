## ADDED Requirements

### Requirement: Confirmar o "já vi isso"
Na análise de um incidente com "já vi isso", uma pessoa identificada SHALL poder responder "era o mesmo problema" ou "não era". A resposta SHALL virar evento do incidente com o autor e o incidente passado, e a página SHALL mostrar a resposta. Uma resposta nova SHALL substituir a anterior nas métricas.

#### Scenario: Era o mesmo
- **WHEN** Ana responde "era o mesmo problema" num incidente que apontou o incidente #12
- **THEN** a timeline mostra a confirmação de Ana e as métricas contam um "já vi isso" confirmado

### Requirement: Avaliação do agente estruturada
O post mortem SHALL ter, além do texto da seção "Avaliação do agente", três campos que uma pessoa preenche:
- a hipótese principal estava certa: sim, não ou sem resposta;
- os minutos até a hipótese certa;
- se a memória ajudou: sim, não ou sem resposta.

Os campos SHALL ser editáveis enquanto o post mortem não está encerrado, com autor e data.

#### Scenario: Avaliar
- **WHEN** Ana marca no post mortem "hipótese principal certa: sim", "8 minutos" e "memória ajudou: sim"
- **THEN** os campos ficam gravados com Ana e a data, e entram nas métricas

### Requirement: Métricas do agente por período
A api SHALL servir `/metricas` com, por semana ou por mês (escolha na página, padrão mês) e para os últimos 6 períodos:
- incidentes resolvidos;
- post mortems aprovados ou encerrados;
- acerto da hipótese principal (certas sobre as avaliadas);
- mediana dos minutos até a hipótese certa;
- "já vi isso" confirmados e recusados;
- vezes que a memória ajudou.

Todas as métricas SHALL sair das respostas humanas e do log, sem LLM. Período sem dado SHALL mostrar "—".

#### Scenario: Mês com avaliações
- **WHEN** no mês houve 4 post mortems avaliados, 3 com a hipótese principal certa
- **THEN** a linha do mês mostra 75% de acerto e a mediana dos minutos informados
