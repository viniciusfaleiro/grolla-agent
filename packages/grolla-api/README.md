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

O processo que precisa responder rápido, sempre: é por ele que os alertas chegam. A api é fina:
autentica e traduz o webhook pelo port `AlertIngress`, chama o caso de uso `receive_alerts` e
renderiza as páginas (Jinja2, sem JS) a partir do `IncidentRepository`. Não tem regra de
negócio nem faz trabalho pesado — investigação, timers e notificações ficam no `grolla-worker`. Depende só do
`grolla-core`; no startup aplica as migrações pendentes e monta as dependências pelo `bootstrap`.

## 🚀 Features

| Área | O que entrega | Endpoint |
|---|---|---|
| **Health check** | `200 {"status": "ok"}` com o banco acessível; `503` sem banco | `GET /health` |
| **Saúde do worker** | `200` se o último heartbeat tem até 2 min, senão `503`; corpo `{"last_heartbeat": <iso ou null>}` | `GET /health/worker` |
| **Webhook de alertas** | HTTP Basic: `401` sem credencial válida (sem efeito), `422` em payload inválido ou não-JSON, `202` depois de processar os alertas | `POST /webhooks/alerts` |
| **Lista de incidentes** | HTML server-side: título, severidade, estado, serviços e detecção, mais recentes primeiro | `GET /incidents` |
| **Página do incidente** | HTML: dados do incidente; a análise (em andamento, concluída ou parcial com o motivo, com resumo, "O que avaliei", hipóteses com confiança e evidências com link e o plano proposto); a validação logo abaixo (validando as hipóteses, concluída ou parcial com o motivo, com a causa raiz em destaque ou "causa raiz não confirmada" e o que faltou, o veredito de cada hipótese com justificativa e evidências, o plano de ação com alvo, efeito esperado e link e o que avaliei na validação); a causa raiz também aparece perto do título; alertas (status, regra, resumo, origem) e a timeline de eventos; `404` se não existe | `GET /incidents/{id}` |

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
| `GROLLA_CONFIG_FILE` | sim | — |
| `GROLLA_ALERTS_WEBHOOK_USER` | sim | — |
| `GROLLA_ALERTS_WEBHOOK_PASSWORD` | sim | — |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-api
```

- `tests/unit/` — `create_app` recebe `Services` (checagem de banco, heartbeat e os ports), montado
  com os fakes de `grolla.testing`: rotas de saúde, webhook e páginas testadas com `TestClient`,
  sem banco e sem rede; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — `/health` contra Postgres real (Testcontainers).

## 🗂 Layout

```
src/grolla_api/
  app.py     create_app(): rotas HTTP (saúde, webhook, páginas)
  templates/ páginas Jinja2 (base, lista e incidente)
  main.py    entrypoint `grolla-api`: settings → migrações → uvicorn
tests/       unit/ · integration/
Dockerfile
```
