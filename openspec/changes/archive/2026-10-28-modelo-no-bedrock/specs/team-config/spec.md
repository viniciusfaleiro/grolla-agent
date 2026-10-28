## MODIFIED Requirements

### Requirement: Segredos só no ambiente
Segredos SHALL vir só de variáveis de ambiente:
- `GROLLA_TEAMS_WEBHOOK_URL`;
- `GROLLA_ALERTS_WEBHOOK_USER` e `GROLLA_ALERTS_WEBHOOK_PASSWORD`;
- `GROLLA_ANTHROPIC_API_KEY`, exigida só quando `llm.provider` é `anthropic`;
- `GROLLA_SIGNOZ_API_KEY`, usada só pelo container do servidor MCP de telemetria;
- `GROLLA_AZURE_DEVOPS_TOKEN`.

O YAML SHALL NOT conter segredos. Cada processo SHALL exigir só os segredos que usa.

#### Scenario: Senha do webhook ausente
- **WHEN** a api sobe sem `GROLLA_ALERTS_WEBHOOK_PASSWORD`
- **THEN** o processo não sobe e a mensagem nomeia a variável

#### Scenario: Chave do LLM ausente
- **WHEN** o worker sobe com `llm.provider: anthropic` (ou sem `llm.provider`) e sem `GROLLA_ANTHROPIC_API_KEY`
- **THEN** o processo não sobe e a mensagem nomeia a variável

#### Scenario: Bedrock sem chave da Anthropic
- **WHEN** o worker sobe com `llm.provider: bedrock` e sem `GROLLA_ANTHROPIC_API_KEY`
- **THEN** o processo sobe normalmente

## ADDED Requirements

### Requirement: Provedor do modelo
A seção `llm` do YAML SHALL aceitar `provider`, com `anthropic` (padrão) ou `bedrock`, e `region`, a região da AWS em que o modelo é chamado. Com `bedrock`, `region` SHALL ser obrigatória, e o worker SHALL chamar o modelo pelo Amazon Bedrock com as credenciais da cadeia padrão do SDK da AWS, sem chave de API. Com `anthropic`, `region` SHALL NOT ser aceita. Os IDs de `llm.stages` SHALL ser os do provedor escolhido. Todas as etapas SHALL usar o mesmo provedor e manter os mesmos recursos (ferramentas, thinking adaptativo, `effort`, saída estruturada, cache de prompt e registro de uso por etapa). O YAML SHALL NOT conter credenciais da AWS.

#### Scenario: Investigação pelo Bedrock
- **WHEN** o YAML tem `llm.provider: bedrock` e `llm.region: us-east-1` e um incidente novo é investigado
- **THEN** as chamadas ao modelo vão para o Bedrock em `us-east-1`, com o ID de modelo da etapa, as ferramentas e a saída estruturada, e o uso é registrado com a etapa `investigation`

#### Scenario: Bedrock sem região
- **WHEN** o YAML tem `llm.provider: bedrock` sem `llm.region`
- **THEN** o processo não sobe e a mensagem aponta o campo `llm.region`

#### Scenario: Provedor desconhecido
- **WHEN** o YAML tem `llm.provider: vertex`
- **THEN** o processo não sobe e a mensagem aponta o campo `llm.provider`

#### Scenario: Sem provedor no YAML
- **WHEN** o YAML não tem `llm.provider`
- **THEN** o worker usa a API da Anthropic com `GROLLA_ANTHROPIC_API_KEY`, como antes
