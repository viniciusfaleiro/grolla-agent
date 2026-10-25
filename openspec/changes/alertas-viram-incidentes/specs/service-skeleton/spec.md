## ADDED Requirements

### Requirement: Saúde do worker visível pela api
O `grolla-api` SHALL expor `GET /health/worker`, que responde 200 quando o último heartbeat do worker tem até 2 minutos e 503 quando é mais antigo ou não existe, informando o horário do último heartbeat.

#### Scenario: Worker parado
- **WHEN** o último heartbeat tem 5 minutos
- **THEN** `GET /health/worker` responde 503 com o horário do último heartbeat

#### Scenario: Worker vivo
- **WHEN** o último heartbeat tem 20 segundos
- **THEN** `GET /health/worker` responde 200

### Requirement: Worker entrega a fila de notificações
O `grolla-worker` SHALL rodar um job periódico, a cada 5 segundos, que entrega as notificações pendentes da outbox pelo port `ChatNotifier`.

#### Scenario: Card pendente
- **WHEN** existe um card pendente na outbox e o worker está rodando
- **THEN** o card é entregue em até 10 segundos
