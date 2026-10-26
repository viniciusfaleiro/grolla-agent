## ADDED Requirements

### Requirement: Configuração da memória
O YAML do time SHALL aceitar a seção `memory` com:
- `learning_max_tokens`: limite de tokens do aprendizado de um incidente;
- `study` (opcional): `cron` do estudo diário, `run_max_tokens` (limite de uma rodada de estudo, inicial ou diária), `service_max_tokens` (limite por serviço) e `lookback_days` (janela de deploys e traces do estudo).

A seção `llm.stages` SHALL aceitar as etapas `learning` e `study`; sem elas, SHALL valer o modelo da etapa `investigation`. Sem a seção `memory`, o aprendizado SHALL usar um limite padrão e nenhum estudo do ambiente SHALL rodar.

#### Scenario: Estudo desligado
- **WHEN** o YAML não tem `memory.study`
- **THEN** nenhum estudo roda e o aprendizado ao encerrar continua ligado

#### Scenario: Limite inválido
- **WHEN** o YAML tem `memory.study.run_max_tokens: 0`
- **THEN** o processo não sobe e a mensagem aponta o campo
