# service-skeleton Specification

## Purpose
TBD - created by archiving change fundacao-harness. Update Purpose after archive.
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

