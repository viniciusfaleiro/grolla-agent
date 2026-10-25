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
| **Reset** | Limpa roteiro e gravações entre cenários e volta o LLM para `completa` | `POST /_control/reset` |
| **Modo do LLM** | Troca o comportamento da Messages API fake: `completa` (padrão), `fora_do_ar` ou `lenta`; responde 204 | `POST /_control/llm` `{"mode": "fora_do_ar"}` |
| **Console de testes** | Página para testar à mão: dispara alertas no webhook da api como o SigNoz, resolve, mostra os cards do worker (investigando e análise) como um canal do Teams, põe o Teams aceitando (202) ou falhando (500) e escolhe o roteiro do LLM fake (completa, fora do ar, lenta) | `GET /_console` |
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
respondeu: o card "investigando" e, depois, o card de análise. O renderer entende `TextBlock`
(com quebra de linha e linhas `- item` como lista), `FactSet`, `Container`, `ColumnSet` e
`Action.OpenUrl`.

O seletor **Roteiro do LLM fake** escolhe como o LLM fake responde às próximas análises:
análise completa (padrão), LLM fora do ar ou análise lenta. Os dois últimos geram a análise
parcial. O seletor chama `POST /_control/llm {"mode": "completa" | "fora_do_ar" | "lenta"}`,
e `/_control/reset` volta para `completa`.

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
  main.py      entrypoint `grolla-fakes`
tests/
Dockerfile
```
