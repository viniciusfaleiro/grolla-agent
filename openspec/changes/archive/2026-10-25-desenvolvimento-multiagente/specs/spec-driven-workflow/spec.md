## ADDED Requirements

### Requirement: Changes com vários componentes podem rodar com múltiplos agentes
Quando uma change toca mais de uma área de responsabilidade, o desenvolvimento SHALL poder rodar com um agente por área (domínio, persistência, integrações, web, e2e), cada um no seu próprio git worktree e branch, conversando com os outros por mensagem para combinar contratos. Cada agente SHALL alterar só os caminhos da sua área e SHALL rodar `make check` no próprio worktree antes de entregar. Changes pequenas ou de uma área só SHALL rodar com um agente.

#### Scenario: Agente precisa de algo fora da sua área
- **WHEN** o agente de web precisa de um campo novo num modelo do domínio
- **THEN** ele pede ao agente de domínio por mensagem, em vez de editar o arquivo do domínio

### Requirement: Agente pai fecha contratos e integra
A sessão principal SHALL atuar como agente pai: antes de abrir os agentes, SHALL escrever os contratos da change como código (ports, modelos, endpoints, schema) e commitá-los; SHALL ser o único a alterar arquivos compartilhados (`pyproject.toml`, `uv.lock`, `grolla.bootstrap`, `docker-compose.yml`, `Makefile`); e, no final, SHALL juntar os branches dos agentes, rodar `make e2e` e `make check` e revisar os READMEs. Mudança de contrato depois do commit de contrato SHALL ser combinada por mensagem entre os agentes afetados e registrada no `design.md`.

#### Scenario: Fim do trabalho paralelo
- **WHEN** todos os agentes entregam seus branches com `make check` verde
- **THEN** o agente pai junta os branches, roda `make e2e` e `make check` e só então a change pode ser arquivada

### Requirement: Artefatos da change preparam o trabalho paralelo
As regras de `design` em `openspec/config.yaml` SHALL exigir uma seção **Contratos**. As regras de `tasks` SHALL exigir, numa change com múltiplos agentes, um grupo 0 de contratos feito pelo agente pai, um grupo por agente identificado pelo nome do papel e um grupo final de integração do agente pai; numa change de um agente só, o `tasks.md` SHALL declarar isso no início.

#### Scenario: Change multiagente gera tarefas
- **WHEN** uma change que toca domínio, persistência e web gera o seu `tasks.md`
- **THEN** o arquivo tem o grupo 0 de contratos, um grupo para cada um dos três agentes e o grupo final de integração
