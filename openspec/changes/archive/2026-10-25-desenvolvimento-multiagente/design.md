## Context

O harness do S1 assume um agente só. O plano de slices tem changes grandes (S2 em diante) com frentes independentes. A sessão principal do Claude Code consegue lançar agentes em worktrees isolados e trocar mensagens com eles.

## Goals / Non-Goals

**Goals:** regras simples para paralelizar sem conflito de arquivo nem de contrato; o agente pai sempre fecha com o e2e.

**Non-Goals:** ver a proposta.

## Contratos

Esta change não cria contratos de código. O contrato que ela define é de processo: os caminhos de cada papel (tabela abaixo) e a lista de arquivos compartilhados do agente pai.

| Papel | Caminhos |
|---|---|
| `grolla-dominio` | `packages/grolla-core/src/grolla/{domain,application}/`, `packages/grolla-core/src/grolla/testing/`, `packages/grolla-core/tests/{unit,contracts}/` do domínio |
| `grolla-persistencia` | `packages/grolla-core/src/grolla/adapters/postgres/` e seus testes |
| `grolla-integracoes` | `packages/grolla-core/src/grolla/adapters/<fornecedor externo>/`, seus testes e o protocolo do fornecedor em `e2e/fakes/` |
| `grolla-web` | `packages/grolla-api/` |
| `grolla-e2e` | `e2e/scenarios/`, `e2e/test_scenarios.py` |
| agente pai | `pyproject.toml`, `uv.lock`, `grolla.bootstrap`, `docker-compose.yml`, `Makefile`, `CLAUDE.md`, READMEs do pai, `openspec/` |

O `grolla-worker` não tem papel fixo: os jobs dele entram com o papel cuja lógica eles disparam (ex.: entrega do outbox com `grolla-integracoes`), indicado no `tasks.md`.

## Decisions

1. **Papel por área, não por pacote.** Quase todo o trabalho cai no `grolla-core`; um agente por pacote deixaria a api e o worker ociosos. *Mais simples considerada:* um agente por pacote; descartada por isso.
2. **Contrato como código antes de abrir os agentes.** Combinar por conversa durante o trabalho é o que mais atrasa e mais gera retrabalho. *Mais simples considerada:* contrato só no `design.md`; descartada porque os agentes precisam de tipos reais para compilar e testar.
3. **Definições em `.claude/agents/`.** Cada arquivo traz o dono, o que não pode tocar e as regras do projeto, para o agente não depender de redescobrir o contexto. *Mais simples considerada:* instruções no prompt de cada lançamento; descartada porque se perdem entre changes.
4. **Só o agente pai roda o e2e.** As portas do compose são fixas. *Mais simples considerada:* manter assim; tornar as portas configuráveis só se o e2e virar gargalo.

## Risks / Trade-offs

- [Custo de tokens maior: cada agente começa do zero] → usar só em changes com frentes realmente independentes; o `tasks.md` declara o modo.
- [Conflito de merge em arquivo compartilhado] → só o agente pai altera esses arquivos.
- [Contrato muda no meio] → mudança combinada por mensagem e registrada no `design.md`; o agente pai confere na integração.
