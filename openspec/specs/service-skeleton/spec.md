# service-skeleton Specification

## Purpose

Definir os processos deployáveis (api e worker), como sobem, aplicam migrações e expõem a própria saúde.

## Requirements
### Requirement: A api expõe health check
O `grolla-api` SHALL responder `GET /health` com status 200 e `{"status": "ok"}` quando o banco estiver acessível, e com status 503 quando não estiver.

#### Scenario: Banco disponível
- **WHEN** `GET /health` é chamado com o banco acessível
- **THEN** a resposta é 200 com `{"status": "ok"}`

#### Scenario: Banco indisponível
- **WHEN** `GET /health` é chamado sem acesso ao banco
- **THEN** a resposta é 503

### Requirement: O worker processa jobs da fila no banco
O `grolla-worker` SHALL consumir jobs da fila mantida no Postgres. Neste slice SHALL existir o job periódico `heartbeat`, que grava no banco o horário obtido do port `Clock`.

#### Scenario: Heartbeat gravado
- **WHEN** o worker está rodando
- **THEN** um registro de heartbeat é gravado no banco pelo menos uma vez por minuto

### Requirement: Schema versionado por migrações
O schema do banco SHALL ser criado e evoluído só por migrações Alembic do `grolla-core`. Os deployables SHALL aplicar as migrações pendentes antes de começar a atender.

#### Scenario: Banco vazio
- **WHEN** a api sobe contra um banco vazio
- **THEN** as migrações são aplicadas e o health check responde 200

### Requirement: Configuração vem do ambiente
Os deployables SHALL ler a configuração de variáveis de ambiente validadas na inicialização, e SHALL falhar ao subir com mensagem clara se uma variável obrigatória faltar ou for inválida.

#### Scenario: URL do banco ausente
- **WHEN** a api sobe sem a variável de URL do banco
- **THEN** o processo termina com erro nomeando a variável ausente

### Requirement: Uma imagem por deployable
Cada deployable SHALL ter Dockerfile próprio que produz uma imagem contendo só o próprio pacote, o `grolla-core` e as dependências de runtime.

#### Scenario: Imagem do worker sem a api
- **WHEN** a imagem do `grolla-worker` é construída
- **THEN** o pacote `grolla-api` não está presente nela

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

