## ADDED Requirements

### Requirement: Configuração do console
O YAML SHALL aceitar a seção opcional `console` com:
- `max_tokens` por resposta (padrão 60000);
- `daily_max_tokens` (padrão 500000);
- `timeout_seconds` (padrão 120).

Também SHALL aceitar a etapa `console` em `llm.stages`, que herda de `warroom_chat` e, sem ela, de `investigation`. Sem `console`, a página SHALL dizer que o console está desligado.

#### Scenario: Console desligado
- **WHEN** o YAML não tem `console`
- **THEN** `/console` diz que o console está desligado nesta instância
