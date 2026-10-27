## MODIFIED Requirements

### Requirement: Aprender pelo console
No console, a ferramenta `remember` SHALL gravar a nota com o autor da pergunta como fonte e a confiança `aprendido`, e SHALL recusar a nota com cara de ordem, como no chat do war room.

#### Scenario: Time ensina pelo console
- **WHEN** Ana escreve no console "o batch de conciliação roda às 3h e sobe o p99"
- **THEN** a memória ganha a nota `aprendido` com Ana como fonte, e a resposta diz "Guardei: …"
