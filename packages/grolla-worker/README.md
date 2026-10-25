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
| **Análise de incidentes** | Reivindica os pedidos de análise e investiga cada um com o modelo de linguagem até a fila esvaziar. Cada pedido termina como análise concluída ou parcial, com o card no canal. Roda até `investigation.max_parallel` rodadas ao mesmo tempo, cada uma pegando pedidos diferentes | periódico `run_analyses` (`* * * * * */5`, fila `analysis`, `queueing_lock`) |
| **Análises presas** | Encerra como parcial ("interrompida") a análise que ficou em andamento além de `timeout + 2 min`, quando o worker caiu no meio dela | periódico `reap_stuck_analyses` (todo minuto, fila `analysis`, `lock`) |
| **Entrega de cards** | Entrega a outbox de notificações no canal do time (Adaptive Card via Workflow do Teams), em ordem por incidente, com retentativa e espera crescente; registra no incidente a entrega ou a desistência | periódico `deliver_notifications` (`* * * * * */5`, uma rodada por vez com `queueing_lock` e `lock`) |

## 🏗 Arquitetura

O processo roda **dois laços do procrastinate**:

| Fila | Concorrência | Jobs |
|---|---|---|
| `default` | 1 | `heartbeat`, `deliver_notifications` |
| `analysis` | `investigation.max_parallel` | `run_analyses`, `reap_stuck_analyses` |

Assim uma análise de minutos nunca atrasa o heartbeat nem a entrega dos cards. O `main.py`
trata SIGINT/SIGTERM e encerra os dois laços com graça, porque com dois laços os handlers de
sinal do procrastinate brigariam.

## ⚡ Como rodar

```bash
docker compose up -d db
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
export GROLLA_TEAMS_WEBHOOK_URL=https://...  # Workflow do Teams do canal do time
export GROLLA_CONFIG_FILE=deploy/credenciadora.yaml
export GROLLA_ANTHROPIC_API_KEY=...
export GROLLA_AZURE_DEVOPS_TOKEN=...          # PAT somente leitura
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
| `GROLLA_CONFIG_FILE` | sim | — (YAML do time: `investigation`, `llm`, `signoz`, `azure_devops`) |
| `GROLLA_ANTHROPIC_API_KEY` | sim | — |
| `GROLLA_AZURE_DEVOPS_TOKEN` | sim | — (PAT somente leitura) |
| `GROLLA_ANTHROPIC_BASE_URL` | não | API da Anthropic (o e2e aponta para o `grolla-fakes`) |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-worker
```

- `tests/unit/` — a lógica do job (`beat`) com `FakeClock`, sem banco; os jobs de entrega e de
  análise com os fakes dos ports; as filas e os crons de cada job; os dois laços parando no
  SIGINT/SIGTERM; e a recusa em subir sem `GROLLA_DATABASE_URL`.
- `tests/integration/` — um heartbeat enfileirado é processado pelo worker contra Postgres real.

## 🗂 Layout

```
src/grolla_worker/
  heartbeat.py  lógica do job, independente da fila
  app.py        build_app(): registra os jobs no procrastinate, nas filas default e analysis
  main.py       entrypoint `grolla-worker`: settings → migrações → os dois laços do worker
tests/          unit/ · integration/
Dockerfile
```
