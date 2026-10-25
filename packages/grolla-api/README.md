<div align="center">

<img src="../../docs/assets/grolla.svg" alt="Grolla" width="96">

# grolla-api

**Deployable HTTP do agent-grolla: recebe os webhooks de alerta e serve o war room, o console e o post mortem.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-uvicorn-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Kind](https://img.shields.io/badge/tipo-deployable-orange)](#-como-rodar)
[![Docker](https://img.shields.io/badge/imagem-grolla--api-2496ED?logo=docker&logoColor=white)](Dockerfile)

</div>

---

## ✨ O que é

O processo que precisa responder rápido, sempre: é por ele que os alertas chegam. Não faz
trabalho pesado — investigação, timers e notificações ficam no `grolla-worker`. Depende só do
`grolla-core`; no startup aplica as migrações pendentes e monta as dependências pelo `bootstrap`.

## 🚀 Features

| Área | O que entrega | Endpoint |
|---|---|---|
| **Health check** | `200 {"status": "ok"}` com o banco acessível; `503` sem banco | `GET /health` |

## ⚡ Como rodar

```bash
docker compose up -d db
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
uv run grolla-api                      # http://localhost:8000/health
```

Imagem própria (só a api e o core dentro):

```bash
docker build -f packages/grolla-api/Dockerfile -t grolla-api:local .
```

| Variável | Obrigatória | Padrão |
|---|---|---|
| `GROLLA_DATABASE_URL` | sim | — |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-api
```

- `tests/unit/` — `create_app` recebe a checagem de banco como função, então o 200/503 é testado
  sem banco e sem rede; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — `/health` contra Postgres real (Testcontainers).

## 🗂 Layout

```
src/grolla_api/
  app.py     create_app(): rotas HTTP
  main.py    entrypoint `grolla-api`: settings → migrações → uvicorn
tests/       unit/ · integration/
Dockerfile
```
