## Context

O repositório tem só a spec de produto (`docs/initial-spec.md`) e o openspec inicializado. A máquina local tem Docker 29 e Python 3.10; `uv` e `gitleaks` não estão instalados. A referência de harness é o projeto eng-performance (Java/Gradle), adaptado para Python. As decisões de estrutura, cobertura e extras foram fechadas com o usuário antes deste slice.

## Goals / Non-Goals

**Goals:**
- Um workspace onde `make check` e `make e2e` funcionam desde o primeiro commit.
- Fronteiras de arquitetura e de fornecedor impostas por ferramenta, não por disciplina.
- Esqueleto mínimo e real dos dois deployables, para que os gates tenham código de verdade para verificar.

**Non-Goals:**
- Regras de negócio, ports de fornecedor e protocolos de fornecedor no `grolla-fakes` (ver proposta).
- CI, deploy na EC2 e frontend.

## Decisions

### 1. uv workspace com três pacotes
`packages/grolla-core`, `packages/grolla-api` e `packages/grolla-worker` são membros de um workspace uv, com um único `uv.lock` no pai. Cada pacote usa layout `src/` e declara só as dependências que usa; por isso o core não consegue importar FastAPI, e o worker não consegue importar a api.
- *Mais simples considerada:* um pacote único com dois entrypoints. Descartada porque o usuário quer um pacote por deployable, e as dependências por pacote dão uma fronteira física de graça.
- *Descartada:* quebrar o core em vários pacotes (domain, application, cada adapter). Cerimônia demais; o import-linter cobre as camadas dentro do core.

### 2. Um `Makefile` como interface única
`make fmt`, `make check`, `make e2e` e `make test`. Cada alvo chama `uv run <ferramenta>`. Configurações das ferramentas (`ruff`, `pyright`, `pytest`, `coverage`, `importlinter`) ficam centralizadas no `pyproject.toml` do pai.
- *Mais simples considerada:* scripts shell soltos. Descartada porque o Make já dá alvos nomeados, dependências entre alvos e é universal.

### 3. Pisos de cobertura por pacote com uma passada só
Um `pytest --cov` único gera os dados; um script curto em `scripts/coverage_floors.py` lê o relatório JSON e aplica os pisos por prefixo de módulo (`grolla.domain` 90/80, `grolla.application` 80/70). O coverage.py só tem `fail_under` global, por isso o script.
- *Mais simples considerada:* `fail_under` global. Descartada porque o requisito é piso por camada.
- Pacotes ainda vazios (domain e application quase não têm código neste slice) passam com 100% vacuamente; o script trata "sem linhas" como aprovado.

### 4. Arquitetura: import-linter com quatro tipos de contrato
- `layers`: `grolla.bootstrap` > `grolla.adapters` > `grolla.application` > `grolla.domain`.
- `forbidden`: domain e application não importam libs de infraestrutura (lista na spec).
- `independence`: subpacotes de `grolla.adapters` não se importam.
- `forbidden`: `grolla_worker` não importa `grolla_api`, e vice-versa.

### 5. Checagem de vocabulário por script próprio
`scripts/check_vocabulary.py` percorre os `.py` de `domain` e `application`, quebra o texto em tokens (separando por `_`, camelCase e não alfanuméricos) e falha se algum token bater com a lista de fornecedores. São poucas linhas, sem dependência.
- *Mais simples considerada:* `grep -i`. Descartada porque pega falso positivo dentro de palavras ("teams" em "steams", "ado" em "dado").

### 6. Suítes de contrato como classe base de teste
A suíte de um port é uma classe pytest abstrata em `grolla-core/tests/contracts/`, com uma fixture `subject` que cada implementação sobrescreve. Fake e adapter real herdam da mesma classe.
- O `Clock` é o único port deste slice. Ele isola o relógio do sistema, que é infraestrutura: os timers da spec (5 min, 15 min, 3 min de triagem) precisam de tempo controlável nos testes. Ele também prova o mecanismo das suítes de contrato antes do primeiro port de fornecedor.

### 7. Persistência e fila
SQLAlchemy Core + psycopg 3 (async), Alembic com migrações no `grolla-core`. A primeira migração cria o schema da fila do procrastinate (o SQL vem do próprio procrastinate, executado direto no driver) e a tabela `heartbeats`. Assim existe um único sistema de migração, versionado, e as atualizações futuras do schema da fila entram como novas migrações. Api e worker rodam as migrações no startup; o Alembic não tem lock próprio, então a função `migrate` segura um `pg_advisory_xact_lock` durante o upgrade, e dois processos subindo juntos não colidem.
- *Mais simples considerada:* um container separado só para migrar. Adiada; vira necessária só se o startup ficar lento.
- *Descartada:* deixar o procrastinate aplicar o próprio schema (`apply_schema`), porque ele não é idempotente e ficaria fora do versionamento.

### 8. Configuração
`pydantic-settings` lendo variáveis com prefixo `GROLLA_`. Neste slice só `GROLLA_DATABASE_URL` e `GROLLA_LOG_LEVEL`. O YAML por time da spec entra no slice que precisar dele.

### 9. e2e
- `docker-compose.yml` no pai com os serviços `db`, `api`, `worker` e `fakes`; `api`, `worker` e `fakes` ficam no profile `e2e`, então `docker compose up -d db` continua subindo só o banco para desenvolvimento.
- `grolla-fakes` vive em `e2e/fakes/`, com app FastAPI pequeno e Dockerfile próprio. É membro do workspace: um lock só, um pip-audit só e os mesmos gates de código. Ele nunca entra numa imagem de produção, porque cada imagem instala só o próprio pacote (`uv sync --package`), e o import-linter proíbe o core e os deployables de importá-lo. Expõe a API de controle (`POST /_control/scenario`, `GET /_control/requests`, `POST /_control/reset`) e uma superfície genérica que grava toda requisição e responde pelo roteiro do cenário; os slices de fornecedor acrescentam os protocolos.
- *Considerada:* projeto fora do workspace, com lock próprio. Descartada na implementação: dois locks e duas auditorias para manter, sem ganho de isolamento.
- O runner (`e2e/test_scenarios.py`) parametriza um teste por YAML; os passos `given`/`when`/`then` são um conjunto pequeno de ações (`http`, `wait_for_db_row`, `expect_status`) que cresce conforme os slices precisarem.
- `make e2e` usa `trap` para sempre rodar `docker compose down -v`.
- O e2e não roda com pytest-socket; o bloqueio de rede vale só para os unitários (`tests/unit`).

### 10. Supply-chain
`pip-audit` roda sobre os requisitos exportados do `uv.lock` (com hashes, sem instalar nada), incluindo as dependências de dev e do `grolla-fakes`. `gitleaks` roda como binário local em modo `detect` no repositório.
- *Alternativa considerada:* rodar o gitleaks por imagem Docker, sem instalar binário. Mantida como fallback no Makefile se o binário não existir.

### 11. Orientação aos agentes
O `CLAUDE.md` segue a estrutura do eng-performance: o que é o produto, estado atual, tabela de arquitetura, fornecedores e ports, gates com a regra "nunca commitar vermelho", fluxo openspec obrigatório, bump de versão e comandos comuns. Ele abre com o princípio de **simplicidade**: a solução mais simples que cumpre a spec vence; sem abstração especulativa; port só para isolar infraestrutura externa.

## Risks / Trade-offs

- [Pyright strict em código com SQLAlchemy e procrastinate gera ruído de tipos] → Usar os stubs disponíveis; onde faltar, isolar a chamada no adapter e tipar a fronteira, sem desligar o modo strict globalmente.
- [pip-audit pode quebrar o build por CVE nova numa dependência transitiva, sem mudança nossa] → Comportamento desejado; a correção é atualizar a dependência. Ignorar uma CVE exige registro com justificativa no `pyproject.toml`.
- [e2e lento por construir imagens] → Cache de camadas do Docker com `uv sync` antes de copiar o código; dependências mudam pouco.
- [Testcontainers e o Docker 29 do host] → No eng-performance foi preciso baixar o piso da API do daemon (`fix-docker-min-api.sh`). Verificar na primeira execução; se repetir, trazer o mesmo script.
- [Hooks não rodam se o desenvolvedor não ativar o `core.hooksPath`] → O `make setup` ativa os hooks; o `CLAUDE.md` instrui o agente a rodar os gates sem depender do hook.

## Migration Plan

Não há sistema anterior. Rollback é descartar o branch.

## Open Questions

- Instalar `uv` e `gitleaks` na máquina local é pré-requisito da primeira tarefa e depende da sua autorização.
