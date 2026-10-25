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
| **Reset** | Limpa roteiro e gravações entre cenários | `POST /_control/reset` |
| **Console de testes** | Página para testar à mão: dispara alertas no webhook da api como o SigNoz, resolve, mostra os cards do worker como um canal do Teams e põe o Teams aceitando (202) ou falhando (500) | `GET /_console` |
| **Superfície fake** | Qualquer outro método/caminho: grava e responde pelo roteiro | `/{qualquer caminho}` |

```json
{"routes": [{"method": "POST", "path": "/pager", "status": 202, "body": {"ok": true}}]}
```

### Protocolos de fornecedor

| Fornecedor | Como aparece no e2e | Desde |
|---|---|---|
| **Teams** | O worker posta os cards em `POST /teams/webhook`; o cenário declara a rota com 202 e confere os cards com `expect_fake_requests` | S2 |
| **SigNoz** | Não passa pelo fake: o próprio runner chama `POST /webhooks/alerts` da api com payloads no formato do SigNoz | S2 |

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
respondeu.

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
  console.html página do console (HTML + JS, sem build)
  main.py      entrypoint `grolla-fakes`
tests/
Dockerfile
```
