## ADDED Requirements

### Requirement: Cards no canal do time
O agente SHALL postar no canal do time um card (Adaptive Card via Workflow do Teams) em três momentos: incidente novo, severidade elevada e incidente normalizado. O card de incidente novo SHALL conter o título, a severidade, os serviços, o resumo e a descrição do alerta como evidência, o link para a origem do alerta e o link para a página do incidente. Reenvios e agrupamentos sem mudança de severidade SHALL NOT gerar card.

#### Scenario: Incidente novo
- **WHEN** um alerta abre um incidente P2 do `capture-api`
- **THEN** o canal recebe um card com "P2", "capture-api", a evidência do alerta e o link da página do incidente

#### Scenario: Agrupamento sem mudança
- **WHEN** um segundo alerta P2 do mesmo serviço entra no incidente
- **THEN** nenhum card novo é postado

### Requirement: Entrega por outbox com retentativa
Cada card SHALL ser gravado numa fila de saída (outbox) na mesma transação da mudança que o originou. O `grolla-worker` SHALL entregar a fila em ordem por incidente, com retentativa e espera crescente quando o canal falhar, e SHALL registrar no incidente a entrega ou a desistência após o limite de tentativas.

#### Scenario: Teams fora do ar
- **WHEN** o canal responde erro nas duas primeiras tentativas e aceita na terceira
- **THEN** o card é entregue uma vez só e o incidente registra a entrega

#### Scenario: Desistência
- **WHEN** o canal falha em todas as tentativas permitidas
- **THEN** o incidente registra a falha de entrega e o card sai da fila
