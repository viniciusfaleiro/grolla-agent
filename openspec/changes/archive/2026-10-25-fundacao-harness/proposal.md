## Why

O agent-grolla vai decidir quem é acionado às 3h da manhã, então precisa nascer com gates que impeçam regressão e erosão de arquitetura antes de qualquer regra de negócio existir. Este é o slice 1 do roadmap: montar o workspace, os três pacotes e o harness completo (no modelo do eng-performance), provando ponta a ponta que tudo roda junto.

## What Changes

- Projeto pai como **uv workspace**: `docker-compose.yml`, `Makefile`, `hooks/`, `openspec/`, `e2e/`, `evals/`, `CLAUDE.md`, `README.md`, `.editorconfig`, `.gitignore`. Versão única no `pyproject.toml` do pai.
- Três pacotes em `packages/`:
  - `grolla-core`: biblioteca com `domain`, `application`, `adapters` e `bootstrap` de composição. Neste slice, só o port `Clock` (com fake e adapter de sistema) e o acesso ao Postgres com a primeira migração Alembic.
  - `grolla-api`: deployable FastAPI com `GET /health` (checa o banco) e imagem Docker própria.
  - `grolla-worker`: deployable procrastinate com um job `heartbeat` que grava no banco, e imagem Docker própria.
- **Gates em `make check`**: ruff format, ruff check, cobrindo também `e2e/` e `scripts/`, (zero warnings, regras do bandit), pyright strict, import-linter, checagem de vocabulário de fornecedor, pytest com pytest-socket nos unitários, integração com testcontainers, cobertura com branch (domain 90/80, application 80/70), suítes de contrato por port, pip-audit, gitleaks e `openspec validate --all --strict`.
- **Camada e2e no projeto pai** (`make e2e`): sobe api, worker e postgres reais mais o container `grolla-fakes`, carrega cenários YAML e confere os efeitos. Neste slice, um cenário trivial: a api responde saudável e o worker processa o heartbeat.
- **Git hooks** via `core.hooksPath`: pre-commit roda format e lint; pre-push roda `make check`.
- **Obrigatoriedade do openspec**: regra no `CLAUDE.md` e no README; `openspec/config.yaml` com contexto e regras de artefato; validação estrita dentro do `make check`.

## Non-goals

- Nenhuma regra de negócio do grolla: alertas, incidentes, severidade, Spike.sh, Teams, investigação, war room e post mortem ficam para os próximos slices.
- Nenhum port além de `Clock`. Os demais (`AlertIngress`, `Pager`, `Telemetry` etc.) nascem nos slices que os usam, com suíte de contrato própria.
- Nenhum adapter de fornecedor (SigNoz, Teams, Spike.sh, Azure DevOps, AWS, Anthropic). O `grolla-fakes` só ganha os protocolos de fornecedor quando o slice correspondente precisar.
- Sem pipeline de CI; os gates rodam localmente e pelos hooks.
- Sem deploy na EC2; o compose deste slice é para ambiente local e e2e.
- Sem frontend além do `/health`.
- Sem plano de slices (`openspec/roadmap.md`); ele é discutido depois deste slice.

## Capabilities

### New Capabilities

- `quality-gates`: o que `make check` e `make e2e` verificam, os pisos de cobertura e o comportamento dos hooks.
- `architecture-boundaries`: camadas hexagonais, isolamento de fornecedores (ports por capacidade, adapters independentes, bootstrap como único ponto de ligação, checagem de vocabulário) e suítes de contrato por port.
- `system-tests`: a camada e2e do projeto pai, o container `grolla-fakes` e o formato dos cenários.
- `service-skeleton`: os deployables `grolla-api` e `grolla-worker`, o health check, o heartbeat e as migrações de banco.
- `spec-driven-workflow`: obrigatoriedade do fluxo openspec e convenções de versão e commit.

### Modified Capabilities

(nenhuma; é o primeiro slice)

## Impact

- **Repositório**: cria toda a estrutura; `docs/initial-spec.md` passa a ser a spec de produto referenciada pelo openspec.
- **Dependências novas**: uv, Python 3.12 (instalado pelo uv), FastAPI, uvicorn, SQLAlchemy, Alembic, psycopg, procrastinate, pydantic, pydantic-settings; dev: ruff, pyright, import-linter, pytest, pytest-socket, pytest-cov, testcontainers, pip-audit, gitleaks.
- **Máquina local**: requer instalar o `uv` (hoje ausente; o Python do sistema é 3.10) e o binário do `gitleaks`. Docker já está disponível.
- **Referências na spec de produto**: seções "Arquitetura", "Stack proposta", "Configuração por deployment" e "Segurança, custo e guardrails" de `docs/initial-spec.md`.
