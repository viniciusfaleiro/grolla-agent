> Change de um agente só: toca o adapter `grolla.adapters.anthropic` (papel `grolla-integracoes`) e o
> `grolla.bootstrap`, sem port novo, sem migração e sem mudança na api, nos fakes ou nos cenários.

## 1. Adapter

- [x] 1.1 `AnthropicApi`, `Bedrock` e `Connection` em `grolla.adapters.anthropic`; `ClaudeLanguageModel` recebe `connection` no lugar de `api_key`/`base_url`.
- [x] 1.2 `_client()` devolve `AsyncAnthropic` ou `AsyncAnthropicBedrock(aws_region=…)` com o mesmo transporte, retries e timeout; alias do union nas anotações de `_loop`, `_once` e `_concrete`.
- [x] 1.3 Testes: as suítes de contrato de `LanguageModel` rodando também com `Bedrock`, transporte que responde em `/model/<id>/invoke` e credenciais AWS falsas; teste do host, do ID da etapa no caminho e da assinatura SigV4.

## 2. Bootstrap

- [x] 2.1 `LlmConfig.provider` (`anthropic` | `bedrock`, padrão `anthropic`) e `LlmConfig.region`, com a validação (`bedrock` exige `region`, `anthropic` recusa); testes dos cenários de provedor desconhecido, sem região e sem provedor.
- [x] 2.2 `WorkerSettings.anthropic_api_key` opcional; o container exige a chave só com `anthropic` (`ConfigError` nomeando `GROLLA_ANTHROPIC_API_KEY`) e monta a `Connection`; testes.

## 3. Docs e fechamento

- [x] 3.1 `deploy/README.md`: seção do Bedrock (acesso ao modelo, IDs e inference profile, `bedrock:InvokeModel` só na credencial do worker e restrita aos modelos, cotas); exemplo comentado em `deploy/credenciadora.yaml`; `docs/decisoes.md`.
- [x] 3.2 Revisar e atualizar os READMEs do `grolla-core` e do pai (Features, Stack, Roadmap e badge de status).
- [x] 3.3 `make e2e` com todos os cenários verdes.
- [x] 3.4 `make fmt && make check` com tudo verde.
