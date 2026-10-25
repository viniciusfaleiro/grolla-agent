<div align="center">

<img src="../../docs/assets/grolla.svg" alt="Grolla" width="96">

# grolla-fakes

**Substitutos controláveis de todos os serviços externos, para os testes integrados do agent-grolla rodarem sem tocar em nada real.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-uvicorn-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Kind](https://img.shields.io/badge/tipo-s%C3%B3%20e2e-lightgrey)](#-o-que-é)
[![Docker](https://img.shields.io/badge/imagem-grolla--fakes-2496ED?logo=docker&logoColor=white)](Dockerfile)

</div>

---

## ✨ O que é

Um container só que faz o papel dos serviços externos no `make e2e`. O runner controla o estado
pela API de controle; qualquer outra requisição é gravada e respondida pelo roteiro do cenário
(404 se o roteiro não prevê a rota). **Nunca** entra numa imagem de produção.

Cada slice de fornecedor acrescenta aqui o protocolo dele (webhook do SigNoz, API da Anthropic,
Teams, Spike.sh, MCPs).

## 🚀 Features

| Área | O que entrega | Endpoint |
|---|---|---|
| **Roteiro** | Carrega as rotas e respostas do cenário | `POST /_control/scenario` |
| **Gravação** | Lista as requisições recebidas, em ordem | `GET /_control/requests` |
| **Reset** | Limpa roteiro, gravações e os roteiros do SigNoz e do Azure DevOps e volta o LLM para `completa` | `POST /_control/reset` |
| **Modo do LLM** | Troca o comportamento da Messages API fake: `completa` (padrão), `fora_do_ar` ou `lenta`; responde 204 | `POST /_control/llm` `{"mode": "fora_do_ar"}` |
| **Roteiro da telemetria** | Fixa respostas ou falhas das ferramentas do MCP do SigNoz | `POST /_control/signoz` |
| **Roteiro dos deploys** | Limita repositórios, fixa builds ou derruba a API do Azure DevOps | `POST /_control/azure-devops` |
| **Console de testes** | Página para testar à mão: dispara alertas no webhook da api como o SigNoz, resolve, mostra os cards do worker (investigando, análise e causa raiz) como um canal do Teams, põe o Teams aceitando (202) ou falhando (500) e escolhe o roteiro do LLM fake para a análise (completa, fora do ar, lenta) e para a validação (completa, fora do ar, lenta, inconclusiva) | `GET /_console` |
| **Superfície fake** | Qualquer outro método/caminho: grava e responde pelo roteiro | `/{qualquer caminho}` |

```json
{"routes": [{"method": "POST", "path": "/pager", "status": 202, "body": {"ok": true}}]}
```

### Protocolos de fornecedor

| Fornecedor | Como aparece no e2e | Desde |
|---|---|---|
| **Teams** | O worker posta os cards em `POST /teams/webhook`; o cenário declara a rota com 202 e confere os cards com `expect_fake_requests` | S2 |
| **SigNoz** | Não passa pelo fake: o próprio runner chama `POST /webhooks/alerts` da api com payloads no formato do SigNoz | S2 |
| **Anthropic** | Messages API em `POST /anthropic/v1/messages` (o worker usa `GROLLA_ANTHROPIC_BASE_URL=http://fakes:9000/anthropic`). Cada chamada fica gravada. No modo `completa`, a primeira resposta pede `query_metric`, `recent_deploys` e `search_logs` (tool_use), com argumentos tirados do `input_schema` recebido e o serviço tirado da linha "Serviços:" do brief. Quando chegam os tool_results, devolve o JSON da análise citando trechos deles. `fora_do_ar` responde 529 overloaded com `retry-after-ms: 10`, para o SDK desistir em milissegundos. `lenta` espera 60 s, além do timeout de 15 s do e2e | S3 |
| **SigNoz (MCP)** | Servidor MCP streamable HTTP (lib `mcp`, sem estado) em `/signoz/mcp`, com as ferramentas de leitura que o adapter usa: `signoz_aggregate_traces`, `signoz_search_logs`, `signoz_search_traces` e `signoz_get_service_top_operations`, respostas no formato do Query Builder v5. Cada chamada é gravada como `POST /signoz/mcp` com `{"tool", "arguments"}`; ferramenta desconhecida (por exemplo de escrita) responde erro | S3 |
| **Azure DevOps** | `GET /azure-devops/{org}/{project}/_apis/git/repositories/{repo}` e `GET .../_apis/build/builds` (exigem PAT em Basic; sem ele, 401). Cada chamada é gravada com a query em `body` | S3 |

**História padrão do S3.** Sem roteiro, as duas pontas contam a mesma história, para o LLM fake e
os evals terem o que relacionar: todo repositório existe e tem **1 deploy bem-sucedido há 20 min**
(`{repo}-deploy`, branch `main`, por Fulana de Tal); a partir de **18 min atrás** a taxa de erro do
serviço sobe de 0,4% para 7%, o p99 vai de 180 ms para 2.400 ms, a vazão fica em 20 chamadas/s e
os logs mostram 31× `connection timeout to {serviço}-db:5432 after 5000ms (pool exhausted)`, com
12 spans `POST /authorize` com erro de timeout no banco. Tudo é calculado sobre a janela pedida.

**Roteiros** (o `POST /_control/reset` limpa os dois):

```json
POST /_control/signoz
{"responses": {"signoz_search_logs": {"status": "success", "data": {"data": {"results": []}}}},
 "failing": ["signoz_search_traces"]}

POST /_control/azure-devops
{"repositories": ["authorizer-api"], "builds": [{"buildNumber": "20261025.3", "...": "..."}], "status": 200}
```

- `responses`: resposta fixa por ferramenta (o JSON vira o texto do resultado); `failing`: as
  ferramentas que respondem erro (vira "não foi possível consultar" na análise).
- `repositories`: os únicos nomes que existem (`null` = todos; um serviço fora da lista dá
  "serviço sem repositório mapeado"); `builds`: lista fixa no formato da API (`null` = o deploy
  padrão, filtrado por `minTime`); `status`: diferente de 200 faz a API inteira responder esse
  status.

## ⚡ Como rodar

Normalmente sobe pelo `make e2e` (serviço `fakes`, porta 9000). Sozinho:

```bash
uv run grolla-fakes          # http://localhost:9000
```

### Console de testes

```bash
docker compose --profile e2e --project-name grolla-demo up -d --build --wait
# abra http://localhost:9000/_console   (derrube antes do make e2e: as portas são as mesmas)
docker compose --profile e2e --project-name grolla-demo down --volumes
```

O console chama a api pelo servidor (`GROLLA_FAKES_API_URL`, `GROLLA_FAKES_API_USER`,
`GROLLA_FAKES_API_PASSWORD`; os padrões são os do e2e), então o navegador não precisa de CORS
nem vê a senha. O canal lista o que chegou em `POST /teams/webhook`, com o status que o fake
respondeu: o card "investigando", o card de análise e, depois da validação, o card "Causa
raiz" (causa raiz, hipóteses com o veredito, plano de ação e o que faltou). O renderer entende `TextBlock`
(com quebra de linha e linhas `- item` como lista), `FactSet`, `Container`, `ColumnSet` e
`Action.OpenUrl`.

O seletor **Roteiro do LLM fake** escolhe como o LLM fake responde às próximas análises:
análise completa (padrão), LLM fora do ar ou análise lenta. Os dois últimos geram a análise
parcial. O seletor chama `POST /_control/llm {"mode": "completa" | "fora_do_ar" | "lenta"}`,
e `/_control/reset` volta para `completa`.

O seletor **Validação das hipóteses** faz o mesmo para a segunda fase: causa raiz confirmada
(padrão), LLM fora do ar, validação lenta ou inconclusiva (termina sem causa raiz, com o que
faltou). Fora do ar e lenta geram a validação parcial. Ele chama
`POST /_control/llm {"verification_mode": "completa" | "fora_do_ar" | "lenta" | "inconclusiva"}`;
os dois campos do controle são opcionais e um não mexe no outro.

## 🧪 Testes

```bash
uv run pytest e2e/fakes      # in-process, sem rede
```

## 🗂 Layout

```
src/grolla_fakes/
  app.py       create_app(): API de controle + superfície fake
  state.py     roteiro, requisições gravadas e alertas do console
  console.py   rotas do console de testes
  llm.py       Messages API fake e o controle do modo
  console.html página do console (HTML + JS, sem build)
  signoz_mcp.py servidor MCP fake do SigNoz + /_control/signoz
  azure_devops.py API de builds fake do Azure DevOps + /_control/azure-devops
  main.py      entrypoint `grolla-fakes`
tests/
Dockerfile
```
