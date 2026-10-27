## ADDED Requirements

### Requirement: Juntar incidentes
No war room, uma pessoa identificada SHALL poder juntar o incidente num outro incidente aberto. O incidente juntado SHALL ficar descartado com o motivo "juntado em <título do outro>". O principal SHALL receber os serviços e os alertas disparando do juntado, e a severidade dele SHALL ficar a pior das duas. Os dois SHALL registrar o evento com o autor, e o canal SHALL receber o card. Juntar um incidente nele mesmo, ou num incidente que não está aberto, SHALL ser recusado.

#### Scenario: Juntar a sugestão
- **WHEN** Ana clica em "Juntar" no incidente da `captura-api`, sugerido como o mesmo do `pedidos-api`
- **THEN** o da `captura-api` fica descartado com "juntado em …", e o do `pedidos-api` passa a ter os dois serviços e a pior severidade
