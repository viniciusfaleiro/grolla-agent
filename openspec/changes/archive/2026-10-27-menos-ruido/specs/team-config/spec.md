## ADDED Requirements

### Requirement: Configuração da triagem
O YAML SHALL aceitar a seção opcional `noise` com `window_minutes` (padrão 30), maior que zero, e a etapa `triage` em `llm.stages`, que herda de `investigation`. Sem `noise`, nenhuma triagem SHALL rodar.

#### Scenario: Triagem desligada
- **WHEN** o YAML não tem `noise`
- **THEN** nenhum pedido de triagem é gravado e o incidente segue como antes
