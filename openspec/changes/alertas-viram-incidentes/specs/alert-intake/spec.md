## ADDED Requirements

### Requirement: Webhook autenticado recebe alertas
O `grolla-api` SHALL expor `POST /webhooks/alerts`, que aceita o payload de notificação do SigNoz (formato compatível com Alertmanager, com a lista `alerts`). Requisições sem as credenciais configuradas (HTTP Basic) SHALL ser recusadas com 401 sem efeito nenhum. Um payload válido SHALL ser respondido com 202 depois de gravado.

#### Scenario: Credenciais ausentes
- **WHEN** um payload chega sem o cabeçalho de autenticação ou com senha errada
- **THEN** a resposta é 401 e nenhum alerta é gravado

#### Scenario: Payload válido
- **WHEN** um payload autenticado com dois alertas chega
- **THEN** a resposta é 202 e os dois alertas são processados

#### Scenario: Payload malformado
- **WHEN** um payload autenticado não segue o formato esperado
- **THEN** a resposta é 422 e nada é gravado

### Requirement: Alertas são normalizados num modelo neutro
Cada alerta recebido SHALL ser convertido num alerta neutro com: fingerprint, status (disparando ou resolvido), nome da regra, serviço, labels, resumo, descrição, início, fim e link para a origem. O payload bruto do alerta SHALL ser gravado junto, para histórico e aprendizado futuro. O serviço SHALL vir do primeiro label presente da lista configurada em `service_labels` (padrão: `service_name`, `service.name`); alerta sem nenhum deles SHALL ser agrupado sob o serviço `desconhecido`.

#### Scenario: Alerta com serviço
- **WHEN** chega um alerta com label `service_name: authorizer-api`
- **THEN** o alerta neutro tem serviço `authorizer-api` e o payload bruto fica gravado

#### Scenario: Label de serviço configurado
- **WHEN** a instância tem `service_labels: [app]` e chega um alerta com label `app: captura`
- **THEN** o alerta neutro tem serviço `captura`

### Requirement: Só alertas do time da instância
A instância SHALL aceitar só alertas cujos labels contêm todos os pares do filtro configurado (ex.: `team: credenciadora`). Alertas fora do filtro SHALL ser ignorados sem criar nem alterar incidente.

#### Scenario: Alerta de outro time
- **WHEN** chega um alerta com label `team: liquidacao` numa instância filtrada por `team: credenciadora`
- **THEN** nenhum incidente é criado ou alterado

### Requirement: Alerta reenviado não duplica nada
O mesmo alerta, identificado por fingerprint e início, SHALL ser processado uma vez só. Reenvios (o SigNoz repete notificações enquanto o alerta dispara) SHALL NOT gerar evento, card nem incidente novos.

#### Scenario: Reenvio periódico
- **WHEN** o mesmo alerta disparando chega três vezes
- **THEN** existe um incidente, um evento de alerta recebido e um card
