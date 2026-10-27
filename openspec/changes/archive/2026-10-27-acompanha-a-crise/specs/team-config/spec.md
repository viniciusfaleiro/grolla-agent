## ADDED Requirements

### Requirement: Configuração do acompanhamento e do impacto
O YAML SHALL aceitar a seção opcional `follow_up` com `severities` (padrão `[P0, P1]`), `cycle_minutes` (padrão 5), `quiet_minutes` (padrão 15), `stable_minutes` (padrão 15), `max_cycles` (padrão 24), `cycle_max_tokens` (padrão 40000), `cycle_timeout_seconds` (padrão 180) e `max_tokens_per_incident` (padrão 600000), e a etapa `follow_up` em `llm.stages`, que herda de `investigation`. Sem `follow_up`, nenhum ciclo SHALL rodar.

O YAML SHALL aceitar a seção opcional `impact` com `currency` (padrão `BRL`) e `services.<serviço>.per_minute`, maior que zero.

#### Scenario: Acompanhamento desligado
- **WHEN** o YAML não tem `follow_up`
- **THEN** nenhum ciclo roda e o resto do incidente funciona como antes

#### Scenario: Valor por minuto inválido
- **WHEN** o YAML tem `impact.services.pedidos-api.per_minute: 0`
- **THEN** o processo não sobe e a mensagem aponta o campo
