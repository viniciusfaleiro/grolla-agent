## ADDED Requirements

### Requirement: Incidente aberto por uma pessoa
O núcleo SHALL aceitar a abertura manual de um incidente com título, serviço, severidade e autor. O incidente SHALL nascer detectado (ou informado, pela severidade, como no alerta), sem alerta disparando, com o evento de abertura manual com o autor e a origem, e com o pedido de análise inicial. Título, serviço ou autor vazios SHALL ser recusados.

#### Scenario: Abrir manualmente
- **WHEN** Ana abre o incidente "pedidos-api lento", serviço `pedidos-api`, P2
- **THEN** o incidente aparece na lista com Ana como autora da abertura e a análise inicial pedida
