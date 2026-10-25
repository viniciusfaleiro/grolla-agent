<div align="center">

<img src="../../docs/assets/grolla.svg" alt="Grolla" width="96">

# grolla-worker

**Deployable de jobs do agent-grolla: timers dos incidentes, ciclos de investigação e entrega de notificações, numa fila dentro do próprio Postgres.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![procrastinate](https://img.shields.io/badge/fila-procrastinate-6f42c1)](https://procrastinate.readthedocs.io/)
[![Kind](https://img.shields.io/badge/tipo-deployable-orange)](#-como-rodar)
[![Docker](https://img.shields.io/badge/imagem-grolla--worker-2496ED?logo=docker&logoColor=white)](Dockerfile)

</div>

---

## ✨ O que é

O processo do trabalho lento e pesado. Se ele travar ou reiniciar, a api continua recebendo
alertas. Depende só do `grolla-core`; no startup aplica as migrações pendentes e consome a fila
do procrastinate.

## 🚀 Features

| Área | O que entrega | Job |
|---|---|---|
| **Heartbeat** | Grava o horário do `Clock` na tabela `heartbeats` a cada 15 s — prova de vida do worker | periódico `heartbeat` (`* * * * * */15`) |
| **Entrega de cards** | Entrega a outbox de notificações no canal do time (Adaptive Card via Workflow do Teams), em ordem por incidente, com retentativa e espera crescente; registra no incidente a entrega ou a desistência | periódico `deliver_notifications` (`* * * * * */5`, uma rodada por vez com `queueing_lock` e `lock`) |

## ⚡ Como rodar

```bash
docker compose up -d db
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
export GROLLA_TEAMS_WEBHOOK_URL=https://...  # Workflow do Teams do canal do time
uv run grolla-worker
```

Imagem própria (só o worker e o core dentro):

```bash
docker build -f packages/grolla-worker/Dockerfile -t grolla-worker:local .
```

| Variável | Obrigatória | Padrão |
|---|---|---|
| `GROLLA_DATABASE_URL` | sim | — |
| `GROLLA_TEAMS_WEBHOOK_URL` | sim | — (URL do Workflow do Teams que posta no canal; é segredo) |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-worker
```

- `tests/unit/` — a lógica do job (`beat`) com `FakeClock`, sem banco; o job de entrega com os
  fakes da outbox e do chat; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — um heartbeat enfileirado é processado pelo worker contra Postgres real.

## 🗂 Layout

```
src/grolla_worker/
  heartbeat.py  lógica do job, independente da fila
  app.py        build_app(): registra os jobs no procrastinate (heartbeat e entrega de cards)
  main.py       entrypoint `grolla-worker`: settings → migrações → worker
tests/          unit/ · integration/
Dockerfile
```
