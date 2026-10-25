## Why

O usuário precisa construir rápido. Slices como o S2 têm várias frentes independentes (domínio, persistência, integrações, web, e2e) que um agente só faria em sequência. Com regras claras de contrato, posse e integração, vários agentes podem trabalhar em paralelo sem conflito.

## What Changes

- Novo modo de desenvolvimento para changes que tocam mais de um componente: um agente por área de responsabilidade, cada um no seu git worktree, conversando entre si para combinar contratos.
- **Agente pai** (a sessão principal) fecha os contratos em código antes de abrir os agentes, é dono dos arquivos compartilhados, junta os branches no final e roda o `make e2e`.
- `CLAUDE.md` ganha a seção "Desenvolvimento com múltiplos agentes".
- `openspec/config.yaml` ganha regras: `design.md` com seção **Contratos**; `tasks.md` com grupo 0 de contratos, grupos por agente e grupo final de integração do agente pai; changes pequenas declaram que rodam com um agente só.
- `.claude/agents/` ganha uma definição por papel: `grolla-dominio`, `grolla-persistencia`, `grolla-integracoes`, `grolla-web`, `grolla-e2e`.

## Non-goals

- Paralelizar changes pequenas ou de um componente só; elas continuam com um agente.
- Rodar vários stacks e2e ao mesmo tempo (as portas são fixas; só o agente pai roda o e2e).
- Automatizar o merge entre branches; o agente pai integra e resolve conflitos.
- Mudar a arquitetura ou os gates.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `spec-driven-workflow`: acrescenta os requisitos do desenvolvimento com múltiplos agentes e da estrutura de contratos e tarefas por agente.

## Impact

- `CLAUDE.md`, `README.md` (como contribuir), `openspec/config.yaml`, `.claude/agents/`.
- Nenhum código de produção muda.
