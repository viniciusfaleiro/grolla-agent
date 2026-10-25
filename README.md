<div align="center">

<img src="docs/assets/grolla.svg" alt="Grolla, o mascote do agent-grolla" width="160">

# agent-grolla

**Agente de SRE para adquirência e cartões: detecta, investiga, aciona o on-call e conduz o incidente até o post mortem — chegando antes do humano, com as evidências organizadas.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/badge/uv-workspace-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-api-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)](docker-compose.yml)

[![Architecture](https://img.shields.io/badge/arquitetura-hexagonal-blueviolet)](#-arquitetura)
[![Boundaries](https://img.shields.io/badge/fronteiras-import--linter-informational)](#-gates-de-qualidade--o-harness)
[![Types](https://img.shields.io/badge/tipos-pyright%20strict-1f6feb)](#-gates-de-qualidade--o-harness)
[![Tests](https://img.shields.io/badge/testes-pytest%20%2B%20Testcontainers%20%2B%20e2e-25A162?logo=pytest&logoColor=white)](#-testes-integrados-e2e)
[![Coverage floor](https://img.shields.io/badge/cobertura-domain%2090%2F80%20%C2%B7%20application%2080%2F70-success)](#-gates-de-qualidade--o-harness)
[![Code style](https://img.shields.io/badge/estilo-ruff-D7FF64?logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Workflow](https://img.shields.io/badge/workflow-spec--driven%20(OpenSpec)-9cf)](#-spec-driven-development)
[![Status](https://img.shields.io/badge/status-S3%20investiga%20cada%20alerta%20%E2%9C%93-brightgreen)](#-roadmap)

</div>

---

## ✨ O que é

O `agent-grolla` acompanha o ambiente de adquirência e cartões 24x7. Ele recebe os alertas do
sistema de observabilidade, decide a severidade, aciona o on-call quando é P0/P1, investiga a
causa com um LLM, conduz o incidente num **war room web** e, no fim, escreve o rascunho do post
mortem para revisão humana.

O nome é uma homenagem: o projeto é a tentativa de ter um agente que faz o que fazia **um dos
melhores profissionais de SRE** com quem o autor já trabalhou — o Grolla. Chegar primeiro,
separar sinal de ruído, levantar hipóteses com evidência e manter o registro de tudo o que
aconteceu durante a crise.

E, como um bom SRE, ele **aprende com o tempo**: guarda o que viu em cada incidente e o que
descobriu sobre o ambiente, e usa isso para investigar melhor — inclusive avisando o time quando
um problema parecido já aconteceu ("já vi isso no incidente #N").

Duas decisões atravessam o projeto inteiro:

- **Somente leitura.** O agente sugere ações; quem executa e quem encerra o incidente é uma pessoa.
- **O núcleo decide, o LLM investiga.** Estado, severidade e acionamento são determinísticos;
  o LLM só devolve hipóteses, que entram como eventos.

A **[spec de produto](docs/initial-spec.md)** é a fonte de verdade do *o quê*; as specs vivas
por capacidade ficam em [`openspec/`](openspec/).

## 🚀 Features

| Área | O que entrega | Ponto de entrada |
|---|---|---|
| **Webhook de alertas** | Recebe os alertas do SigNoz com HTTP Basic, filtra pelo time e deduplica reenvios | `POST /webhooks/alerts` |
| **Incidentes** | Agrupa alertas do mesmo serviço numa janela de 15 min, aplica as regras de severidade do YAML (só sobe) e muda o estado (Detectado / Informado) | `receive_alerts` |
| **Investigação de todo alerta** | Card "Estou investigando" na hora e, em até 5 min, "Análise pronta": o que avaliou (métricas, logs, traces, deploys), hipóteses com confiança e evidência, plano proposto e botões para o incidente e o SigNoz. Falha, timeout ou orçamento viram **análise parcial**, nunca silêncio | job `run_analyses` · `make evals` |
| **Ferramentas neutras** | `query_metric`, `search_logs`, `find_traces` e `recent_deploys` sobre o MCP do SigNoz (só leitura, lista fechada) e a API de builds do Azure DevOps | `grolla.application.investigation.Toolbox` |
| **Cards no Teams** | Investigando, análise, severidade elevada e normalizado, entregues por outbox com retentativa e em ordem por incidente | job periódico `deliver_notifications` |
| **Páginas** | Lista de incidentes e página com estado, severidade, análise completa, alertas e timeline | `GET /incidents` · `GET /incidents/{id}` |
| **Log de eventos** | Todo fato vira evento imutável (trigger no banco): base da auditoria e da memória do agente | tabela `incident_events` |
| **Configuração por time** | YAML com filtro, labels de serviço, janela e regras; segredos só no ambiente | `GROLLA_CONFIG_FILE` |
| **Health check** | Api saudável só com o banco acessível; worker vivo pelo último heartbeat (2 min) | `GET /health` · `GET /health/worker` |
| **Heartbeat do worker** | Prova de vida gravada no banco a cada 15 s | job periódico `heartbeat` |
| **Deploy manual** | Compose de produção, config da Credenciadora e passo a passo | [`deploy/`](deploy/) |
| **Migrações** | Schema versionado por Alembic, aplicado no startup sob advisory lock | `grolla.adapters.postgres.migrate` |
| **Harness** | Todos os gates num comando só | `make check` |
| **Testes integrados** | Stack completo com fakes dos serviços externos e cenários YAML | `make e2e` |
| **Console de testes** | Dispara alarmes, escolhe o roteiro do LLM fake e mostra os cards do agente como num canal do Teams | `http://localhost:9000/_console` |

> Até o S3: todo alerta vira incidente com "estou investigando" e a análise do agente no Teams.
> Acionamento do on-call, war room e post mortem chegam nos próximos slices — ver [Roadmap](#-roadmap).

## 🏛 Arquitetura

**Hexagonal (ports & adapters)**, imposta pelo **import-linter**: dependências só apontam para
dentro, e cada fornecedor externo fica isolado num adapter próprio, porque as ferramentas
(Teams, AWS, SigNoz, Spike.sh…) podem mudar.

```mermaid
flowchart LR
  api["grolla-api<br/>webhooks + web"] --> boot
  worker["grolla-worker<br/>jobs + timers"] --> boot
  boot["bootstrap<br/>liga port → adapter"] --> adapters
  boot --> app
  adapters["adapters/&lt;fornecedor&gt;<br/>postgres · signoz · teams · anthropic · azure_devops · system_clock"] --> app
  app["application<br/>casos de uso + ports"] --> dom["domain<br/>regras puras"]
  adapters --> dom
```

| Camada / pacote | Responsabilidade | Pode depender de |
|---|---|---|
| `grolla.domain` | Regras de negócio puras — **sem framework, sem infraestrutura, sem nome de fornecedor** | — |
| `grolla.application` | Casos de uso + ports (`Protocol`) com nome de capacidade | `domain` |
| `grolla.adapters.<fornecedor>` | Implementação de um port; **independentes entre si** | `application`, `domain` |
| `grolla.bootstrap` | Composition root — único lugar que liga port → adapter | todos |
| `grolla-api` / `grolla-worker` | Deployables; cada um com imagem própria | `grolla-core` |
| `grolla-fakes` | Fakes dos serviços externos, só no e2e | — |

**Ports por capacidade, nunca por fornecedor:** `AlertIngress`, `AlertSource`, `Telemetry`,
`ChangeHistory`, `CloudInventory`, `Pager`, `ChatNotifier`, `LanguageModel`, `EventLog`,
`IncidentRepository`, `Scheduler`, `DocumentRenderer` e `Clock`. Cada um nasce no slice que o
usa, com suíte de contrato, fake e adapter real. Hoje existem `Clock`, `AlertIngress` e `Telemetry`
(SigNoz), `ChatNotifier` (Teams), `LanguageModel` (Anthropic), `ChangeHistory` (Azure DevOps),
`IncidentRepository`, `NotificationOutbox` e `AnalysisQueue` (Postgres).

## 🧰 Stack

- **Python 3.12** em **uv workspace** (um lock para tudo).
- **FastAPI** + uvicorn na api; **procrastinate** (fila no próprio Postgres) no worker.
- **PostgreSQL 16** via **SQLAlchemy Core** + **psycopg 3**, migrações **Alembic**.
- **pydantic** / **pydantic-settings** para contratos e configuração (`GROLLA_*`).
- **Jinja2** para as páginas server-side; **httpx2** como cliente HTTP dos adapters.
- **Anthropic SDK** (Claude) com laço de ferramentas manual e structured outputs; **mcp** como
  cliente do servidor MCP do SigNoz. Modelo por etapa no YAML.
- **Docker Compose**: uma imagem por deployable; deploy manual numa EC2, uma instância por time
  ([`deploy/`](deploy/)).

## ⚡ Como rodar

**Pré-requisitos:** [uv](https://docs.astral.sh/uv/), Docker, [openspec](https://github.com/Fission-AI/OpenSpec)
e [gitleaks](https://github.com/gitleaks/gitleaks) (sem o binário, o gate usa a imagem Docker).

```bash
# 1. Dependências + git hooks (uma vez por clone)
make setup

# 2. Todos os gates (o Testcontainers sobe o próprio Postgres)
make check

# 3. Stack completo com os fakes + cenários e2e
make e2e
```

Para desenvolver contra um banco local:

```bash
docker compose up -d db    # Postgres em localhost:5433 (grolla/grolla)
export GROLLA_DATABASE_URL=postgresql://grolla:grolla@localhost:5433/grolla
uv run grolla-api          # http://localhost:8000/health
uv run grolla-worker       # grava um heartbeat a cada 15 s
```

| Variável | Obrigatória | Padrão | Uso |
|---|---|---|---|
| `GROLLA_DATABASE_URL` | sim | — | URL libpq do Postgres (`postgresql://…`) |
| `GROLLA_LOG_LEVEL` | não | `INFO` | `DEBUG`, `INFO`, `WARNING` ou `ERROR` |

Sem uma variável obrigatória, o processo não sobe e a mensagem diz qual falta.

## 🗂 Layout

```
packages/
  grolla-core/     domain · application · adapters · bootstrap · testing   # biblioteca
  grolla-api/      deployable HTTP (FastAPI)
  grolla-worker/   deployable de jobs (procrastinate)
e2e/
  fakes/           grolla-fakes: substitutos controláveis dos serviços externos
  scenarios/       cenários YAML (given / when / then)
  test_scenarios.py  runner: um teste por cenário
evals/             evals do LLM (a partir do slice de investigação)
docs/              initial-spec.md (spec de produto) · assets/
openspec/          changes, specs vivas e config do fluxo spec-driven
scripts/           gates próprios (vocabulário, pisos de cobertura) e e2e.sh
hooks/             git hooks versionados
```

## 🛡 Gates de qualidade — o harness

Tudo abaixo roda em **`make check`** e precisa estar verde, nos pacotes, em `e2e/` e em `scripts/`:

- **ruff format** — a formatação é a fonte de verdade (`make fmt` corrige).
- **ruff check** — zero warnings, incluindo as regras de segurança do bandit (`S`).
- **pyright strict** — tipos em todo o código, testes incluídos.
- **import-linter** — camadas, núcleo sem libs de infraestrutura, adapters independentes,
  deployables sem dependência mútua.
- **Vocabulário** — nome de fornecedor em `domain`/`application` quebra o build.
- **pytest** — unitários **sem rede** (pytest-socket) + integração com **Testcontainers**;
  warnings viram erro.
- **Cobertura com branch** — pisos de **90% linha / 80% branch** em `grolla.domain` e
  **80% / 70%** em `grolla.application`.
- **pip-audit** — CVEs conhecidas nas dependências travadas no `uv.lock`.
- **gitleaks** — segredos no repositório.
- **openspec validate --strict** — changes e specs bem formadas.

Os hooks reforçam localmente: `pre-commit` roda format + lint, `pre-push` roda `make check`.

## 🧪 Testes integrados (e2e)

`make e2e` constrói as imagens, sobe **api + worker + db + grolla-fakes** (profile `e2e` do
compose), roda os cenários e derruba tudo — mesmo se algo falhar.

Nenhum serviço externo real é chamado: o `grolla-fakes` faz o papel de SigNoz, Anthropic,
Teams, Spike.sh e MCPs. O runner reinicia os fakes, carrega o roteiro do cenário, executa as
ações e confere os efeitos (respostas, linhas no banco, requisições recebidas pelos fakes).

```yaml
# e2e/scenarios/fumaca.yaml
name: fumaça do stack
given:
  fakes: {}
when: []
then:
  - expect_status: { service: api, path: /health, status: 200 }
  - wait_for_db_row: { sql: SELECT count(*) FROM heartbeats, at_least: 1, timeout_seconds: 60 }
```

Cenário novo é só um YAML novo em `e2e/scenarios/`.

**Testar à mão:** o console de testes do `grolla-fakes` dispara alarmes e mostra os cards do
agente como num canal do Teams.

```bash
docker compose --profile e2e --project-name grolla-demo up -d --build --wait
# http://localhost:9000/_console  ·  incidentes em http://localhost:8000/incidents
```

## 🧭 Spec-driven development

Mudança de comportamento passa pelo **[OpenSpec](openspec/)** — é obrigatório, e
`openspec/specs/` nunca é editado à mão:

1. `/opsx:propose "<ideia>"` → cria a change com `proposal.md`, `specs/`, `design.md`, `tasks.md`.
2. `/opsx:apply` → implementa as tarefas.
3. `/opsx:archive` → arquiva a change e promove os deltas para `openspec/specs/`.

Toda change fecha revisando os READMEs, com `make e2e` e `make check` verdes.

## 🤝 Como contribuir

1. **Branch** a partir da `master` — nunca commite direto nela.
2. **Ative os hooks** uma vez por clone: `make setup`.
3. **Simplicidade primeiro** — a solução mais simples que cumpre a spec; sem abstração especulativa.
4. **Respeite as fronteiras** — fornecedor só dentro de `grolla.adapters.<fornecedor>`.
5. **Mantenha os gates verdes** — nunca desligue regra, suprima aviso ou baixe piso para passar.
6. **Atualize os READMEs** dos componentes que você tocou.
7. **Mais de uma área na mesma change?** Ela roda com um agente por área e um agente pai que
   fecha os contratos, integra e roda o e2e (ver `CLAUDE.md`).
8. **Commits** — uma linha imperativa curta, em português; `make check` antes do push.

## 🗺 Roadmap

Construído em **slices de valor**: cada um fecha uma funcionalidade de ponta a ponta e vai para
produção ([`openspec/roadmap.md`](openspec/roadmap.md)):

- **S1 — Fundação** · workspace, harness, esqueleto da api e do worker, e2e com fakes ✅
- **S2 — Alertas viram incidentes** · deduplicados, agrupados, com severidade, card no Teams, página do incidente e console de testes ✅ (aceite em produção junto com o S3)
- **S3 — O agente investiga cada alerta** · "estou investigando" em segundos e, em até 5 min, a análise: o que avaliou, hipóteses com evidência, plano proposto e botão para o incidente ✅ (aceite em produção junto com o S2)
- **S4 — On-call acionado sozinho** · Spike.sh para P0/P1 (começa em modo sombra) e ações no war room
- **S5 — Agente acompanha a crise** · investigação profunda de P0/P1, incidentes parecidos, updates, impacto financeiro, chat e proposta de encerramento
- **S6 — Post mortem em minutos** · rascunho automático, edição, aprovação e PDF
- **S7 — O agente aprende** · memória do ambiente e de incidentes passados: "já vi isso no incidente #N"
- **S8–S11** · menos ruído (agrupamento/triagem pelo LLM) · console de operação · transcrição de call · investigação na AWS

## 📄 Licença

Ainda sem arquivo de licença — todos os direitos reservados ao autor até que um seja adicionado.
