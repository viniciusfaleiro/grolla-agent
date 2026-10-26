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
renderiza as páginas (Jinja2; o único JS é o HTMX, servido como estático) a partir do
`IncidentRepository`. No war room, as ações e as mensagens viram chamadas aos casos de uso `act`
e `ask` da `grolla.application.war_room`; a api só lê o form e o autor e devolve o fragmento. Não tem regra de
negócio nem faz trabalho pesado — investigação, timers e notificações ficam no `grolla-worker`. Depende só do
`grolla-core`; no startup aplica as migrações pendentes e monta as dependências pelo `bootstrap`.

## 🚀 Features

| Área | O que entrega | Endpoint |
|---|---|---|
| **Health check** | `200 {"status": "ok"}` com o banco acessível; `503` sem banco | `GET /health` |
| **Saúde do worker** | `200` se o último heartbeat tem até 2 min, senão `503`; corpo `{"last_heartbeat": <iso ou null>}` | `GET /health/worker` |
| **Webhook de alertas** | HTTP Basic: `401` sem credencial válida (sem efeito), `422` em payload inválido ou não-JSON, `202` depois de processar os alertas | `POST /webhooks/alerts` |
| **Lista de incidentes** | HTML server-side: título, severidade, estado em português, serviços e detecção, mais recentes primeiro | `GET /incidents` |
| **War room** | A página do incidente: cabeçalho com estado em português, severidade, duração (até agora ou até o encerramento), IC e TTA/TTM/TTR (vazios enquanto não há horário) e a causa raiz em destaque; os botões só das ações permitidas no estado; o chat com o agente; a análise (em andamento, concluída ou parcial com o motivo, com resumo, "O que avaliei", hipóteses com confiança e evidências e o plano proposto); a validação (causa raiz ou "causa raiz não confirmada" e o que faltou, veredito por hipótese, plano de ação e o que avaliei na validação); alertas e a timeline com o autor de cada evento; `404` se não existe | `GET /incidents/{id}` |
| **Autor (fase 1, sem SSO)** | Sem o cookie `grolla_author`, a página pede o nome; o form grava o cookie (HttpOnly, `SameSite=Lax`, 30 dias, nome URL-encoded) e redireciona (`303`) só para um caminho do próprio site; com o cookie, "Você é Ana (trocar)". Não é autenticação: a página fica na rede interna | `POST /author` |
| **Ações do war room** | Forms HTMX: `assume`, `severity` (`severidade`), `record` (`texto`), `discard-hypothesis` (`titulo`, `motivo`; um botão por hipótese da análise), `mitigate`, `reopen`, `resolve` e `discard` (`motivo`). `200` com o fragmento do cabeçalho e das ações; `401` "informe seu nome" sem cookie; `422` com a mensagem do `InvalidActionError`; `404` para ação ou incidente inexistente. Os erros voltam com `HX-Retarget` para a área de erro do form | `POST /incidents/{id}/actions/{acao}` |
| **Chat com o agente** | Mensagens em ordem, as do agente destacadas e as consultas feitas recolhíveis (`<details>`); pergunta sem resposta aparece como "agente pensando…". O envio (`texto`) responde `200` com o chat e um form limpo, `401` sem cookie, `422` texto vazio, `404` incidente inexistente | `POST /incidents/{id}/chat` |
| **Polling (3 s)** | Fragmentos do chat e da timeline com `hx-trigger="every 3s"`. A URL carrega a versão vista (`?visto=`): sem nada novo, `204` e o HTMX não troca nada (um `<details>` aberto continua aberto) | `GET /incidents/{id}/chat` · `GET /incidents/{id}/timeline` |
| **HTMX** | HTMX 2.0.4 servido como estático, sem build; `htmx-config` faz as respostas `4xx` também aparecerem na página | `GET /static/htmx.min.js` |

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
  com os fakes de `grolla.testing`: rotas de saúde, webhook, páginas e war room testadas com `TestClient`,
  sem banco e sem rede; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — `/health` contra Postgres real (Testcontainers).

## 🗂 Layout

```
src/grolla_api/
  app.py     create_app(): rotas HTTP (saúde, webhook, páginas, war room, autor)
  war_room.py  apresentação do war room: rótulos, durações, ações por estado, leitura do form e do cookie
  templates/ páginas Jinja2 (base, lista, incidente) e fragmentos _war_room, _chat, _chat_messages, _timeline
  static/    htmx.min.js
  main.py    entrypoint `grolla-api`: settings → migrações → uvicorn
tests/       unit/ · integration/
Dockerfile
```
