## ADDED Requirements

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
