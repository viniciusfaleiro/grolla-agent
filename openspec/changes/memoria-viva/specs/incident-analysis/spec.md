## ADDED Requirements

### Requirement: Já vi isso antes
O resultado estruturado da análise SHALL aceitar a referência opcional a um incidente passado, com o motivo da semelhança. Quando a análise traz a referência, o card de análise e a página do incidente SHALL mostrar "Já vi isso", com o título e a data do incidente passado, o link e o motivo, e a causa passada SHALL aparecer como hipótese com "como confirmar". Uma referência a incidente que não está entre os episódios entregues SHALL ser descartada.

#### Scenario: Mesmo problema de novo
- **WHEN** a análise de um incidente de `pedidos-api` aponta o episódio do incidente "pedidos-api com 5xx" de 20/10 com o motivo "mesmo erro de pool após deploy com migration"
- **THEN** o card mostra "Já vi isso: pedidos-api com 5xx (20/10)" com o link e o motivo, e uma hipótese repete a causa daquele incidente com como confirmar

#### Scenario: Referência inventada
- **WHEN** a análise aponta um incidente que não estava entre os episódios entregues
- **THEN** a referência é descartada e o card sai sem "Já vi isso"
