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
| **Validação até a causa raiz** | Reivindica as validações pedidas pela análise inicial com hipóteses e valida cada hipótese com o modelo de linguagem (as mesmas ferramentas, mais as de nuvem), até a fila esvaziar. Cada validação termina concluída (causa raiz e plano de ação, ou "não confirmada" e o que faltou) ou parcial, com o card "Causa raiz" no canal | periódico `run_verifications` (`* * * * * */5`, fila `analysis`, `queueing_lock`) |
| **Validações presas** | Encerra como parcial ("interrompida") a validação em andamento além de `verification_timeout + 2 min` | periódico `reap_stuck_verifications` (todo minuto, fila `analysis`, `lock`) |
| **Respostas do chat do war room** | Reivindica as perguntas feitas ao agente no chat do war room e responde cada uma com o modelo de linguagem (as mesmas ferramentas da investigação, inclusive as de nuvem), até a fila esvaziar. Toda pergunta termina com uma resposta, ou "não consegui responder: <motivo>" | periódico `run_replies` (`* * * * * */2`, fila `analysis`, `queueing_lock`) |
| **Respostas presas** | Responde "não consegui responder: interrompida" à pergunta que ficou em andamento além do limite, quando o worker caiu no meio | periódico `reap_stuck_replies` (todo minuto, fila `analysis`, `lock`) |
| **Aprender ao encerrar** | Para cada incidente resolvido ou descartado, uma chamada ao modelo, sem ferramentas, escreve o episódio de cada serviço (ruído no descarte) e reescreve os dossiês, com versão; falha é tentada de novo uma vez e fica na página da memória | periódico `run_learnings` (`* * * * * */10`, fila `analysis`) e `reap_stuck_learnings` (todo minuto) |
| **Estudo do ambiente** | Regras de alerta do domínio → serviços (das regras e os ativos com repositório) → estudo de cada serviço com ferramentas e teto próprio, parando no teto da rodada → síntese do ambiente e das integrações | periódico `run_studies` (todo minuto, fila `analysis`, `lock`) e `reap_stuck_studies` (todo minuto) |
| **Indexar a memória (RAG)** | Com `memory.rag`, recorta em trechos por seção os dossiês, episódios e notas novos ou mudados (inclusive o que foi gravado direto no banco), gera os embeddings localmente em lote e grava no índice; apaga os trechos de registros esquecidos e de outro modelo, então trocar o modelo reindexa tudo. Repete enquanto houver lote cheio | periódico `index_memory` (`* * * * * */30`, fila `analysis`, `lock`; só registrado com `memory.rag`) |
| **Estudo inicial e diário** | Na subida, com `memory.study` e nenhum dossiê, pede o estudo inicial; o estudo diário pede uma rodada no `memory.study.cron` (só registrado com `memory.study`) | `ensure_initial_study` no `main.py`; periódico `schedule_daily_study` (fila `default`) |
| **Entrega de cards** | Entrega a outbox de notificações no canal do time (Adaptive Card via Workflow do Teams), em ordem por incidente, com retentativa e espera crescente; registra no incidente a entrega ou a desistência | periódico `deliver_notifications` (`* * * * * */5`, uma rodada por vez com `queueing_lock` e `lock`) |

## 🏗 Arquitetura

O processo roda **dois laços do procrastinate**:

| Fila | Concorrência | Jobs |
|---|---|---|
| `default` | 1 | `heartbeat`, `deliver_notifications`, `schedule_daily_study` |
| `analysis` | `investigation.max_parallel` | `run_analyses`, `reap_stuck_analyses`, `run_verifications`, `reap_stuck_verifications`, `run_replies`, `reap_stuck_replies`, `run_learnings`, `reap_stuck_learnings`, `run_studies`, `reap_stuck_studies`, `index_memory` (com `memory.rag`) |

Assim uma análise de minutos nunca atrasa o heartbeat nem a entrega dos cards. Análises,
validações, respostas do chat e o estudo recebem a memória (`container.memory`), o modelo de embeddings
(`container.embedder`, só com `memory.rag`: busca híbrida, trechos de documentação e o estudo indexando os
`.md` de cada serviço estudado) e os mesmos ports do container, inclusive a nuvem (`container.cloud`): sem a
seção `cloud` no YAML, as ferramentas de nuvem respondem que a consulta não está configurada. O `main.py`
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
| `GROLLA_CONFIG_FILE` | sim | — (YAML do time: `investigation`, `llm`, `signoz`, `azure_devops` e, opcional, `cloud`) |
| `GROLLA_ANTHROPIC_API_KEY` | sim | — |
| `GROLLA_AZURE_DEVOPS_TOKEN` | sim | — (PAT somente leitura) |
| `GROLLA_ANTHROPIC_BASE_URL` | não | API da Anthropic (o e2e aponta para o `grolla-fakes`) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | não | cadeia padrão do SDK da AWS (ou a role da máquina); só com a seção `cloud`, e de um principal somente leitura |
| `GROLLA_LOG_LEVEL` | não | `INFO` |

## 🧪 Testes

```bash
uv run pytest packages/grolla-worker
```

- `tests/unit/` — a lógica do job (`beat`) com `FakeClock`, sem banco; os jobs de entrega, de
  análise e de validação com os fakes dos ports; as filas e os crons de cada job; os dois laços parando no
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
