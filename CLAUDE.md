# CLAUDE.md — agent-grolla

Regras que **todo agente** deve seguir neste repositório. Em conflito, a ordem de
precedência é: pedido explícito do usuário → este arquivo → README.md.

## Princípio central: simplicidade

**A solução mais simples que cumpre a spec vence.**

- Implemente só o que a change atual pede. Nada de generalizar "para o futuro".
- Sem abstração especulativa: interface com uma implementação só, configuração que ninguém usa,
  camada extra "por organização". Se não tem segundo uso hoje, escreva o código direto.
- **Port só para isolar infraestrutura externa** (fornecedor, relógio, banco, fila). Lógica
  interna do domínio não ganha interface.
- Ao desenhar, registre a alternativa mais simples considerada e por que foi descartada (regra
  do `design.md` no openspec).
- Prefira apagar a adicionar. Código que não existe não quebra.

## O que é

Agente de SRE para o ambiente de adquirência e cartões: recebe alertas, decide a severidade,
aciona o on-call, investiga a causa com um LLM, conduz o incidente num war room web e gera o
rascunho do post mortem. É a tentativa de ter um agente que faz o que fazia um dos melhores
profissionais de SRE com quem o autor trabalhou — o Grolla. A spec de produto está em
[`docs/initial-spec.md`](docs/initial-spec.md) e é a fonte de verdade do **o quê**.

Decisões que atravessam tudo: o agente é **somente leitura** (sugere, nunca executa); o
**núcleo determinístico** decide estado, severidade e acionamento, e o **LLM só investiga**;
quem encerra um incidente é sempre um humano.

## Estado atual

Slice 1 (`fundacao-harness`): workspace, harness completo e esqueleto dos deployables. A api
responde `/health`, o worker grava um heartbeat a cada 15 s, o e2e sobe tudo com os fakes.
Nenhuma regra de negócio ainda. Próximo: S2 (alertas viram incidentes) — ver
[`openspec/roadmap.md`](openspec/roadmap.md).

## Estrutura (uv workspace)

| Pacote | Papel | Pode depender de |
|---|---|---|
| `packages/grolla-core` | Biblioteca: `domain`, `application` (casos de uso + ports), `adapters`, `bootstrap`, `testing` (fakes) | — |
| `packages/grolla-api` | Deployable HTTP: webhooks de entrada e app web | `grolla-core` |
| `packages/grolla-worker` | Deployable de jobs: timers, investigação, notificações | `grolla-core` |
| `e2e/fakes` (`grolla-fakes`) | Substitutos dos serviços externos, só no e2e | — |

Cada deployable tem imagem Docker própria, que contém só ele e o core.

## Arquitetura (hexagonal — imposta por import-linter)

Dependências só apontam para dentro. Violou → `make check` quebra.

| Camada | Papel | Pode importar |
|---|---|---|
| `grolla.domain` | Regras de negócio puras. **Sem framework, sem infraestrutura.** | — |
| `grolla.application` | Casos de uso + ports (`Protocol`). | `domain` |
| `grolla.adapters.<fornecedor>` | Um subpacote por fornecedor, **independentes entre si**. | `application`, `domain` |
| `grolla.bootstrap` | Composition root: **único** lugar que liga port → adapter. | todos |

### Fornecedores isolados

Ferramentas externas (SigNoz, Teams, Spike.sh, Azure DevOps, AWS, Anthropic, Postgres) podem
mudar. Por isso:

- **Ports têm nome de capacidade, nunca de fornecedor**: `AlertIngress`, `AlertSource`,
  `Telemetry`, `ChangeHistory`, `CloudInventory`, `Pager`, `ChatNotifier`, `LanguageModel`,
  `EventLog`, `IncidentRepository`, `Scheduler`, `DocumentRenderer`, `Clock`. Cada um nasce no
  slice que o usa.
- **Vocabulário neutro**: `domain` e `application` não citam fornecedor em nenhum nome, string
  ou comentário (`make vocabulary`). Nada de `spike_incident_id` no domínio.
- **Suíte de contrato por port** (`packages/grolla-core/tests/contracts/`): todo adapter real e
  todo fake herdam a mesma suíte. Adapter novo está pronto quando a suíte passa.
- **Ferramentas da investigação do LLM são neutras** (`query_metric`, `search_logs`,
  `find_traces`, `recent_deploys`, `read_file`), implementadas sobre os ports. Trocar o
  fornecedor não muda prompt, eval nem cenário.

## Banco de dados

PostgreSQL 16, SQLAlchemy Core + psycopg 3, migrações **Alembic** em
`grolla/adapters/postgres/migrations` (só avançam). Api e worker aplicam as migrações no
startup, serializadas por advisory lock. A fila de jobs é o procrastinate, com o schema criado
pela migração 0001.

## Gates de qualidade

**Todos em `make check`:** ruff format · ruff check (zero warnings, inclui bandit) · pyright
strict · import-linter · vocabulário · pytest (unitários **sem rede** via pytest-socket +
integração com testcontainers) · pisos de cobertura com branch (**domain 90/80,
application 80/70**) · pip-audit · gitleaks · `openspec validate --all --strict`. Cobre os
pacotes, `e2e/` e `scripts/`.

**`make e2e`:** sobe api, worker, db e `grolla-fakes` (profile `e2e` do compose), roda os
cenários de `e2e/scenarios/*.yaml` e derruba tudo.

## ⛔ Regra de gate — SEMPRE rode o harness antes de subir

Antes de **qualquer** `git commit`/`git push`, e antes de declarar uma tarefa concluída:

```bash
make fmt     # formata (a formatação é a fonte de verdade)
make check   # todos os gates; precisa terminar com "todos os gates verdes"
make e2e     # quando a mudança tem comportamento visível ponta a ponta
```

- **Nunca** commite com gate vermelho. Corrija a causa — não desabilite regra, não adicione
  `noqa`/`pyright: ignore` sem justificativa na mesma linha, não baixe piso de cobertura sem o
  usuário pedir explicitamente.
- Os hooks (`hooks/`, ativados com `make setup`) reforçam isso (pre-commit = format + lint,
  pre-push = `make check`), mas o agente **não** deve depender do hook.
- Testes unitários nunca acessam rede nem serviço real: use os fakes dos ports.
- Mensagens de commit: uma linha imperativa curta, em português.

## Fluxo de trabalho — Spec-Driven Development (openspec) é obrigatório

Toda mudança de comportamento passa pelo **openspec** (`openspec/`, schema `spec-driven`).
Não comece a escrever código de feature sem uma change aprovada. **Nunca** edite
`openspec/specs/` à mão — specs só mudam ao arquivar uma change.

1. `/opsx:propose "<ideia>"` — cria a change com `proposal.md`, `specs/`, `design.md`, `tasks.md`.
2. `/opsx:apply` — implementa seguindo `tasks.md`.
3. `/opsx:archive` — arquiva a change e promove os deltas para `openspec/specs/`.

As regras de artefato estão em `openspec/config.yaml`. As três últimas tarefas de toda change
são: revisar os READMEs, `make e2e` verde e `make fmt && make check` verde.

## 🤖 Desenvolvimento com múltiplos agentes

Quando uma change toca **mais de uma área**, ela roda com um agente por área, em paralelo, e a
sessão principal atua como **agente pai**. Change pequena ou de uma área só roda com um agente
(o `tasks.md` declara o modo no início).

| Papel (`.claude/agents/`) | Dono de |
|---|---|
| `grolla-dominio` | `grolla.domain`, `grolla.application`, `grolla.testing` e os testes deles |
| `grolla-persistencia` | `grolla.adapters.postgres` (log de eventos, projeções, outbox, migrações) |
| `grolla-integracoes` | `grolla.adapters.<fornecedor externo>` + o protocolo dele em `e2e/fakes/` |
| `grolla-web` | `packages/grolla-api/` |
| `grolla-e2e` | `e2e/scenarios/` e `e2e/test_scenarios.py` |

Os jobs do `grolla-worker` ficam com o papel cuja lógica eles disparam, indicado no `tasks.md`.

**Fluxo:**

1. **Contratos primeiro (agente pai).** O `design.md` da change tem a seção **Contratos**. O
   agente pai escreve esses contratos como código (ports, modelos Pydantic, endpoints, migração
   de schema) e faz o **commit de contrato** antes de abrir qualquer agente.
2. **Trabalho paralelo.** Cada agente parte do commit de contrato, num git worktree e branch
   próprios, e altera **só os caminhos do seu papel**. Precisa de algo fora deles? Pede por
   mensagem ao dono. Mudança de contrato é combinada por mensagem entre os agentes afetados e
   registrada no `design.md`.
3. **`make check` por agente.** Cada agente entrega o branch com `make check` verde no próprio
   worktree. Ninguém roda `make e2e` em paralelo (as portas do compose são fixas).
4. **Integração (agente pai).** O agente pai é o único que altera os arquivos compartilhados
   (`pyproject.toml`, `uv.lock`, `grolla.bootstrap`, `docker-compose.yml`, `Makefile`, este
   arquivo, os READMEs do pai e `openspec/`). No fim, ele junta os branches, resolve conflitos,
   **roda `make e2e` e `make check`**, revisa os READMEs e só então a change pode ser arquivada.

## 📘 READMEs — sempre atualizados

O projeto pai e **cada componente** (`grolla-core`, `grolla-api`, `grolla-worker`,
`e2e/fakes`) têm `README.md` no padrão do README do eng-performance: cabeçalho centralizado com
o mascote, nome, frase de resumo em negrito e badges; seções com emoji (o que é, features com
ponto de entrada, arquitetura, stack, como rodar, layout, gates, como contribuir, roadmap).

Toda change **revisa e atualiza** os READMEs dos componentes que tocou e o do pai (features,
roadmap e badge de status) antes de fechar. README desatualizado é bug.

## Versão — bump obrigatório depois de entregar uma feature

A versão vive **só** em `version` do `pyproject.toml` da raiz (os pacotes ficam em `0.0.0` e
não são publicados). Depois de mergear uma change na `master`, incremente o **último dígito**
(0.0.1 → 0.0.2) num commit próprio. Um bump por change entregue; correção dentro da mesma
feature não ganha número. Não promova para 0.1.0 sem o usuário pedir.

## Comandos comuns

```bash
make setup                # hooks + dependências (uma vez por clone)
docker compose up -d db   # Postgres local na porta 5433
make fmt                  # formata e corrige o lint automático
make check                # todos os gates rápidos
make e2e                  # stack completo + cenários
openspec list             # changes ativas
```
