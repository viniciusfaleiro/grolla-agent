## Context

Todo acesso ao LLM passa pelo port `LanguageModel`, implementado por `ClaudeLanguageModel` em
`grolla.adapters.anthropic`. O adapter monta um `anthropic.AsyncAnthropic(api_key, base_url)` a
cada chamada (`_client()`) e usa só a Messages API: `messages.create` com ferramentas,
`thinking={"type": "adaptive"}`, `output_config` (JSON schema e `effort`), `cache_control` de 1 h
no system e na última ferramenta, e `tool_choice: none` no fechamento. O bootstrap lê
`GROLLA_ANTHROPIC_API_KEY` (obrigatória) e `GROLLA_ANTHROPIC_BASE_URL` (só o e2e, para o fake), e
os modelos por etapa vêm de `llm.stages` no YAML.

O SDK instalado (`anthropic` 2.3.0) já traz o `AsyncAnthropicBedrock`, que expõe a mesma
`messages.create`: ele troca a URL para `bedrock-runtime.<região>.amazonaws.com`, põe o modelo no
caminho (`/model/<id>/invoke`) e assina com SigV4 usando o `boto3`, que já é dependência do core.
Pela tabela de disponibilidade da Anthropic, o Bedrock suporta tudo o que o adapter usa:
ferramentas, saída estruturada, thinking adaptativo e `effort`, e cache de 5 min e 1 h com
breakpoints explícitos.

## Goals / Non-Goals

**Goals:**
- Escolher o provedor no YAML do time: `anthropic` (padrão) ou `bedrock`.
- Com `bedrock`, autenticar pela cadeia padrão da AWS, sem segredo novo.
- Manter idênticos o comportamento, os prompts, as ferramentas e o registro de uso.

**Non-Goals:** os da proposta (outros provedores, provedor por etapa, fallback, fake do Bedrock
no e2e, preço do Bedrock).

## Decisions

### 1. O Bedrock é um modo do adapter existente, não um adapter novo

`ClaudeLanguageModel` recebe a conexão e escolhe o cliente em `_client()`. O resto (laço de
ferramentas, schemas, cache, orçamento, mapeamento de erros) é o mesmo código.

- *Alternativa mais simples:* um `if region` dentro de `_client()` com `api_key` opcional. Foi
  descartada porque deixa combinações inválidas (chave e região juntas, ou nenhuma) chegarem ao
  adapter. Duas dataclasses pequenas (decisão 2) custam quatro linhas e o pyright impede a
  combinação errada.
- *Alternativa descartada:* um subpacote `grolla.adapters.bedrock`. Seria quase uma cópia de mil
  linhas para trocar só o cliente HTTP. O fornecedor do modelo e da API continua sendo a
  Anthropic; o Bedrock é o transporte.

### 2. A conexão é um tipo do adapter

```python
@dataclass(frozen=True)
class AnthropicApi:
    api_key: str
    base_url: str | None = None  # só o e2e aponta para o fake


@dataclass(frozen=True)
class Bedrock:
    region: str


type Connection = AnthropicApi | Bedrock
```

`ClaudeLanguageModel(connection: Connection, settings, *, transport=None, ...)` substitui os
parâmetros `api_key` e `base_url`. `_client()` devolve
`anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrock`, com o mesmo `http_client`
(transporte dos testes), `max_retries` e `timeout`. O Bedrock recebe `aws_region=region` e nenhuma
credencial explícita, então o SDK usa a cadeia padrão do boto3 (role do container, variáveis
`AWS_*` ou perfil). As anotações `anthropic.AsyncAnthropic` de `_loop`, `_once` e `_concrete`
passam para um alias desse union.

### 3. Provedor e região no YAML; credenciais fora dele

`LlmConfig` ganha `provider: Literal["anthropic", "bedrock"] = "anthropic"` e
`region: str | None = None`, com um `model_validator`: `bedrock` exige `region`, e `anthropic`
recusa `region`. Isso fica no YAML porque os IDs de modelo de `llm.stages` dependem do provedor:
os dois precisam mudar juntos e ser validados juntos. Nenhum dos dois é segredo.

- *Alternativa considerada:* `GROLLA_LLM_PROVIDER` e `AWS_REGION` no ambiente. Foi descartada
  porque separa o provedor dos IDs de modelo: dá para trocar um sem o outro e só descobrir no
  primeiro incidente.

### 4. A chave da Anthropic só é exigida quando usada

`WorkerSettings.anthropic_api_key` passa a ser `SecretStr | None`. O container, ao montar o
worker, levanta `ConfigError("configuração inválida — GROLLA_ANTHROPIC_API_KEY: obrigatória com
llm.provider anthropic")` quando o provedor é `anthropic` e a chave falta. A mensagem segue o
formato de `load_settings`. Com `bedrock`, a chave é ignorada se vier.

### 5. Testes: a mesma suíte de contrato, com o cliente do Bedrock

`test_claude_language_model.py` já roda a suíte de contrato de `LanguageModel` com um transporte
HTTP falso. Uma segunda classe roda as mesmas suítes com `Bedrock(region="us-east-1")`, um
transporte que responde em `/model/<id>/invoke` e credenciais AWS falsas no ambiente do teste. Um
teste confere que a requisição vai para o host do Bedrock, com o ID da etapa no caminho e
assinatura SigV4 (`Authorization: AWS4-HMAC-SHA256 …`). O e2e não muda.

## Contratos

Change de uma área só (integrações + bootstrap), sem commit de contrato separado.

- **Ports:** nenhum criado ou alterado (`LanguageModel` igual).
- **Adapter** `grolla.adapters.anthropic`: `AnthropicApi`, `Bedrock`, `Connection`; a assinatura
  de `ClaudeLanguageModel.__init__` troca `api_key`/`base_url` por `connection`.
- **Configuração:** `llm.provider` (`anthropic` | `bedrock`, padrão `anthropic`) e `llm.region`
  no YAML; `GROLLA_ANTHROPIC_API_KEY` opcional no ambiente, exigida com `anthropic`.
- **Eventos, endpoints, schema do banco:** sem mudança.

## Risks / Trade-offs

- [O modelo configurado não existe no Bedrock na região, ou o acesso não está liberado na
  conta] → O Bedrock devolve 4xx, o adapter já mapeia para `LanguageModelError` e a análise
  parcial garantida segue. O `deploy/README.md` diz como conferir o acesso ao modelo e o ID
  certo (ou o inference profile) antes de subir.
- [O Bedrock recebe recursos e modelos novos depois da API da Anthropic] → Documentado. Os
  recursos usados hoje já são suportados, e a suíte de contrato no modo Bedrock pega o que mudar
  no SDK.
- [A role do container ganha `bedrock:InvokeModel`] → A permissão fica na credencial base do
  worker, restrita aos ARNs dos modelos usados. As roles somente leitura que a investigação
  assume nas contas não recebem essa permissão. O teste de capacidade continua valendo, porque o
  agente não ganha nenhuma ferramenta nova.
- [Cotas de throughput do Bedrock menores que o pico da investigação] → O retry do SDK já
  cobre o throttling (429). Pedir aumento de cota é passo do deploy, documentado.

## Migration Plan

Sem migração: sem `llm.provider`, tudo segue como hoje. Para passar um time ao Bedrock:
1. Liberar o acesso aos modelos no Bedrock na região escolhida.
2. Dar `bedrock:InvokeModel` à credencial do worker.
3. No YAML, definir `llm.provider: bedrock` e `llm.region`, e trocar os IDs de `llm.stages`.
4. Reiniciar o worker.

O rollback é voltar o YAML e reiniciar.

## Open Questions

Nenhuma.
