## Why

Alguns times só podem consumir LLM pela própria conta AWS: os dados ficam na conta, a cobrança vem
na fatura da AWS e o acesso é por IAM, sem chave de API guardada. Hoje o worker só fala com a API
da Anthropic e exige `GROLLA_ANTHROPIC_API_KEY`. Os modelos continuam sendo o Claude, então a
regra de usar só esse fornecedor não muda: muda só o caminho até o modelo.

## What Changes

- O YAML do time ganha `llm.provider`, com `anthropic` (padrão, como hoje) ou `bedrock`, e
  `llm.region`, a região da AWS onde o modelo é chamado, obrigatória com `bedrock`.
- Com `bedrock`, o worker chama o Claude pelo Amazon Bedrock com as credenciais da cadeia padrão
  da AWS (a role do container), sem chave de API. `GROLLA_ANTHROPIC_API_KEY` passa a ser exigida
  só com `anthropic`.
- Os IDs de modelo de `llm.stages` são os do provedor escolhido. Com `bedrock`, valem o ID do
  modelo no Bedrock ou o de um inference profile, e o código continua sem nenhum ID de modelo.
- Mesmos recursos nos dois provedores: ferramentas, thinking adaptativo, `effort`, saída
  estruturada, cache de prompt de 1 h e o registro de uso por etapa. Prompts, ferramentas,
  limites, cerco de segurança e evals não mudam.
- O pacote de deploy documenta a permissão `bedrock:InvokeModel` (só nos modelos usados), na
  credencial do container e nunca nas roles somente leitura da investigação.

## Non-goals

- Outros provedores de nuvem (Vertex AI, Foundry) e o Claude Platform on AWS. O Claude Platform
  on AWS é a mesma API da Anthropic e pode entrar depois, se algum time pedir.
- Provedor diferente por etapa. A instância inteira usa um provedor só.
- Fallback automático entre provedores quando um falha.
- Credenciais da AWS próprias para o modelo no YAML ou em variáveis novas. Vale a cadeia padrão,
  como na seção `cloud`.
- Fake do Bedrock no e2e. O e2e segue com `anthropic` apontando para o fake. O caminho do Bedrock
  é coberto por testes unitários do adapter, com transporte HTTP falso.
- Mudar a contabilidade de custo para o preço do Bedrock. O uso em tokens por etapa continua
  igual.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `team-config`: o YAML aceita `llm.provider` e `llm.region`. A chave da Anthropic passa a ser
  exigida só com o provedor `anthropic`.

## Impact

- `grolla.adapters.anthropic`: o cliente passa a ser `AsyncAnthropic` ou `AsyncAnthropicBedrock`,
  com o resto da chamada igual. Não há dependência nova: o SDK `anthropic` já traz o cliente do
  Bedrock, e o `boto3` já está no core.
- `grolla.bootstrap`: `LlmConfig` ganha `provider` e `region`, `WorkerSettings.anthropic_api_key`
  fica opcional, e o container valida a combinação e escolhe o cliente.
- `deploy/README.md`, `deploy/credenciadora.yaml` (exemplo comentado), os READMEs do core e do pai
  e `docs/decisoes.md`.
- Domínio, application, api, prompts, evals, fakes e cenários e2e: sem mudança. A arquitetura não
  muda: continua um port (`LanguageModel`) e um adapter por fornecedor, porque o Bedrock aqui é só
  um transporte para o mesmo modelo e a mesma API.
