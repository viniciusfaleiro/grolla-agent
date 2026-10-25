## ADDED Requirements

### Requirement: Fakes da investigação
O `grolla-fakes` SHALL fazer o papel, no e2e e no console, de três serviços:
- a API de mensagens do LLM, com respostas roteirizadas por cenário, incluindo chamadas de ferramenta e o resultado final;
- o servidor MCP de telemetria, com as ferramentas de leitura e respostas roteirizadas;
- a API do histórico de mudanças, com os deploys roteirizados.

Um roteiro padrão SHALL produzir uma análise plausível quando o cenário não define outro.

#### Scenario: Análise ponta a ponta no e2e
- **WHEN** um cenário dispara um alerta com o roteiro padrão dos fakes
- **THEN** o Teams fake recebe o card "investigando" e depois o card de análise do mesmo incidente

#### Scenario: LLM fora do ar no e2e
- **WHEN** o roteiro do cenário faz a API do LLM responder erro
- **THEN** o Teams fake recebe o card de análise parcial

### Requirement: Console mostra a análise
O console de testes SHALL mostrar o card de análise no canal, depois do "investigando". O console SHALL permitir escolher o roteiro do LLM fake: análise completa, LLM fora do ar ou análise lenta (tempo esgotado).

#### Scenario: Escolher LLM fora do ar
- **WHEN** o usuário escolhe "LLM fora do ar" e dispara um alerta
- **THEN** o canal mostra o "investigando" e depois o card de análise parcial
