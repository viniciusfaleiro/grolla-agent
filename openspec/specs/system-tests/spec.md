# system-tests Specification

## Purpose

Testar o sistema inteiro com os serviços externos substituídos por fakes controláveis: cenários automatizados e um console manual.

## Requirements
### Requirement: Stack completo sobe para testes integrados
O projeto pai SHALL expor `make e2e`, que constrói as imagens de `grolla-api` e `grolla-worker`, sobe api, worker, postgres e `grolla-fakes` com o profile `e2e` do compose, roda os cenários e derruba o stack ao final, inclusive em caso de falha.

#### Scenario: Execução completa
- **WHEN** `make e2e` roda numa máquina com Docker
- **THEN** o stack sobe, os cenários executam, o stack é removido e o código de saída reflete o resultado dos cenários

### Requirement: Serviços externos são substituídos por fakes controláveis
Todo serviço externo SHALL ser substituído no e2e pelo container `grolla-fakes`, que SHALL expor uma API de controle para carregar o roteiro de um cenário, consultar as requisições recebidas e reiniciar o estado. Nenhum teste e2e SHALL acessar um serviço externo real.

#### Scenario: Reiniciar entre cenários
- **WHEN** um cenário termina e o próximo começa
- **THEN** o runner reinicia o estado do `grolla-fakes` e as requisições gravadas do cenário anterior não aparecem no seguinte

### Requirement: Cenários declarativos
Os cenários SHALL ficar em `e2e/scenarios/*.yaml`, no formato dado, quando, então (`given`, `when`, `then`). O runner SHALL ser pytest no projeto pai e SHALL gerar um caso de teste por arquivo de cenário.

#### Scenario: Cenário novo é descoberto
- **WHEN** um arquivo YAML novo é adicionado em `e2e/scenarios/`
- **THEN** a próxima execução de `make e2e` roda esse cenário sem mudança de código no runner

### Requirement: Cenário mínimo de fumaça
Neste slice SHALL existir um cenário que prova que a api responde saudável com o banco acessível e que o worker processa o job `heartbeat`, gravando no banco.

#### Scenario: Fumaça verde
- **WHEN** `make e2e` roda o cenário de fumaça
- **THEN** `GET /health` responde saudável e um registro de heartbeat aparece no banco dentro do tempo limite do cenário

### Requirement: Console manual de testes
O `grolla-fakes` SHALL servir em `GET /_console` uma página para testar o agente à mão, que SHALL permitir disparar um alerta no formato do SigNoz contra o webhook da api, resolver um alerta disparado por ela e ver os cards que o worker postou no Teams fake como mensagens de canal. A página SHALL permitir que o Teams fake aceite (202) ou recuse (500) os cards. O console SHALL NOT existir fora do `grolla-fakes`.

#### Scenario: Alarme disparado vira mensagem no canal
- **WHEN** o usuário dispara pelo console um alerta P2 do serviço `captura-api`
- **THEN** a api responde 202 e, depois da entrega do worker, o card com "captura-api" aparece no canal do console

#### Scenario: Resolver pelo console
- **WHEN** o usuário clica em "resolver" num alerta que ele disparou e que é o único do incidente
- **THEN** o mesmo alerta é enviado como resolvido e o card de normalização aparece no canal

#### Scenario: Teams falhando
- **WHEN** o usuário põe o Teams fake para recusar e dispara um alerta
- **THEN** nenhum card aparece como entregue até o Teams voltar a aceitar, e então o card aparece uma vez só

