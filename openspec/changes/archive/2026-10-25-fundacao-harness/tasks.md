## 1. Pré-requisitos e base do repositório

- [x] 1.1 Instalar `uv` e `gitleaks` na máquina local (com autorização do usuário) e instalar Python 3.12 via `uv python install 3.12`.
- [x] 1.2 Criar `.editorconfig`, `.gitignore` e `.python-version` no pai.
- [x] 1.3 Criar o `pyproject.toml` do pai: workspace uv com os três membros, versão `0.0.1` (fonte única), dependências de dev e a configuração centralizada de ruff, pyright, pytest, coverage e import-linter.
- [x] 1.4 Criar `packages/grolla-core`, `packages/grolla-api` e `packages/grolla-worker` com layout `src/`, cada um declarando só as próprias dependências; rodar `uv sync` e versionar o `uv.lock`.

## 2. grolla-core

- [x] 2.1 Criar os pacotes `grolla.domain`, `grolla.application`, `grolla.adapters` e `grolla.bootstrap`.
- [x] 2.2 Definir o port `Clock` em `grolla.application`, o fake controlável e o adapter de relógio do sistema em `grolla.adapters.system_clock`.
- [x] 2.3 Escrever a suíte de contrato do `Clock` em `tests/contracts/` e fazer o fake e o adapter herdarem dela.
- [x] 2.4 Configuração com `pydantic-settings` (`GROLLA_DATABASE_URL`, `GROLLA_LOG_LEVEL`), falhando com mensagem clara se faltar variável obrigatória.
- [x] 2.5 Engine async do SQLAlchemy com psycopg 3 em `grolla.adapters.postgres`; Alembic com a primeira migração (`heartbeats`) e a função que aplica migrações pendentes.
- [x] 2.6 Teste de integração com testcontainers: migrações aplicam num banco vazio e um heartbeat é gravado e lido.
- [x] 2.7 Composição em `grolla.bootstrap`: monta settings, engine e `Clock` para os deployables.

## 3. Deployables

- [x] 3.1 `grolla-api`: app FastAPI com `GET /health` (200 com banco acessível, 503 sem banco), aplicando migrações no startup; testes unitários com fakes e um de integração com testcontainers.
- [x] 3.2 `grolla-worker`: app procrastinate com o job periódico `heartbeat` usando o `Clock`, aplicando migrações no startup; testes unitários e de integração.
- [x] 3.3 Dockerfile de cada deployable (multi-stage com `uv sync --package`), sem o outro deployable na imagem.
- [x] 3.4 `docker-compose.yml` no pai: `db` sempre; `api`, `worker` e `fakes` no profile `e2e`.

## 4. Gates

- [x] 4.1 `Makefile` com `setup` (ativa `core.hooksPath`), `fmt`, `test`, `check` e `e2e`.
- [x] 4.2 Contratos do import-linter: camadas, núcleo sem libs de infraestrutura, independência entre adapters, deployables sem dependência mútua.
- [x] 4.3 `scripts/check_vocabulary.py` com tokenização por `_`, camelCase e não alfanuméricos, e testes do próprio script (detecta `spike_incident_id`, ignora "dado").
- [x] 4.4 `scripts/coverage_floors.py` aplicando os pisos por camada sobre o relatório JSON, e testes do próprio script.
- [x] 4.5 pytest-socket ativo só em `tests/unit`; marcador `integration` para os testes com containers.
- [x] 4.6 pip-audit sobre o lock exportado e gitleaks no repositório, com fallback por imagem Docker se o binário não existir.
- [x] 4.7 `openspec validate --all --strict` dentro do `make check`.
- [x] 4.8 Hooks `hooks/pre-commit` (format + lint) e `hooks/pre-push` (`make check`).
- [x] 4.9 ruff e pyright strict cobrindo também `e2e/` (runner e fakes) e `scripts/`; `grolla-fakes` como membro do workspace, coberto pelo mesmo lock e pela mesma auditoria.
- [x] 4.10 Provar que cada gate falha: introduzir temporariamente uma violação de cada tipo (formatação, lint, tipo, camada, vocabulário, cobertura, rede em unitário, segredo, spec sem cenário), confirmar a falha e reverter.

## 5. Camada e2e

- [x] 5.1 `e2e/fakes/`: app FastAPI com a API de controle (`/_control/scenario`, `/_control/requests`, `/_control/reset`) e Dockerfile próprio.
- [x] 5.2 Runner `e2e/test_scenarios.py`: um teste por YAML, reset dos fakes entre cenários, ações `http`, `wait_for_db_row` e `expect_status`.
- [x] 5.3 Cenário `e2e/scenarios/fumaca.yaml`: health 200 e heartbeat gravado dentro do tempo limite.
- [x] 5.4 `make e2e` com `trap` garantindo `docker compose --profile e2e down -v` mesmo em falha.

## 6. Documentação e orientação aos agentes

- [x] 6.1 `CLAUDE.md`: abre com o princípio de simplicidade; produto, estado atual, tabela de arquitetura e de ports, isolamento de fornecedores, regra de gate (nunca commitar vermelho, nunca baixar piso nem desligar regra), fluxo openspec obrigatório, bump de versão e comandos comuns.
- [x] 6.2 `README.md` do pai no padrão do eng-performance: cabeçalho centralizado com tagline e badges (stack, arquitetura hexagonal, import-linter, testes, pisos de cobertura, ruff, openspec, status), e seções ✨ O que é, 🚀 Features, 🏛 Arquitetura (mermaid + tabela de pacotes e ports), 🧰 Stack, ⚡ Como rodar, 🗂 Layout, 🛡 Gates, 🧪 Testes integrados, 🧭 Spec-driven, 🤝 Como contribuir, 🗺 Roadmap, 📄 Licença.
- [x] 6.3 README de `grolla-core`, `grolla-api`, `grolla-worker` e `e2e/fakes` no mesmo padrão, com o escopo de cada um (responsabilidade, ponto de entrada, configuração, como testar).
- [x] 6.4 Regra no `CLAUDE.md`: todo componente tem README no padrão do eng-performance, e toda change revisa os READMEs que afetou.
- [x] 6.5 `evals/README.md` explicando o propósito da pasta e que ela é preenchida no slice de investigação.

## 7. Fechamento

- [x] 7.1 Revisar todos os READMEs (pai e componentes) contra o que foi de fato entregue.
- [x] 7.2 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 7.3 Rodar `make fmt && make check` e garantir tudo verde.
