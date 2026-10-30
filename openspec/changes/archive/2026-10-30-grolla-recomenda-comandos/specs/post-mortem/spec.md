## ADDED Requirements

### Requirement: Comandos aplicados no post mortem
Os comandos que o time confirmou ter aplicado SHALL entrar no post mortem: na timeline, como item de origem pessoa com o horário, o autor, o passo, o comando e onde rodou; e no dado do rascunho, para o modelo citá-los na resposta ao incidente e no plano de ação. O card de encerramento SHALL listá-los em "Ações do time", com o comando e o lugar.

#### Scenario: Rollback aplicado
- **WHEN** "Ana" confirmou às 15:31 o comando `kubectl scale deploy/antifraude-api --replicas=2 -n loja` no "cluster loja-prod, namespace loja" e o incidente é resolvido
- **THEN** a timeline do post mortem tem o item das 15:31, de origem pessoa, com "Ana", o passo, o comando e o lugar, e o card de encerramento lista o mesmo comando em "Ações do time"
