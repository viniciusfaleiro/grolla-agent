## ADDED Requirements

### Requirement: Cards das ações humanas
O agente SHALL postar no canal um card curto quando alguém assume como IC, muda a severidade ou marca mitigado, citando o autor.

No encerramento, SHALL postar o card "Incidente encerrado", com:
- a duração e o TTR;
- quem encerrou;
- o motivo;
- a causa raiz da validação, se houver;
- as ações registradas pelo time.

No descarte, SHALL postar "Incidente descartado" com o motivo. As mensagens do chat SHALL NOT ir para o canal.

#### Scenario: Encerramento no canal
- **WHEN** "Ana" encerra o incidente com o motivo "rollback do build .3"
- **THEN** o canal recebe "Incidente encerrado" com duração, "por Ana", o motivo, a causa raiz e as ações registradas
