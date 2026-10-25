## ADDED Requirements

### Requirement: Mudança de comportamento passa pelo openspec
Toda mudança de comportamento SHALL passar pelo fluxo `/opsx:propose`, `/opsx:apply` e `/opsx:archive`. Specs em `openspec/specs/` SHALL NOT ser editadas à mão; elas SHALL mudar só ao arquivar uma change. A regra SHALL estar escrita no `CLAUDE.md` e no `README.md`.

#### Scenario: Agente recebe pedido de feature
- **WHEN** um agente recebe o pedido de uma feature nova
- **THEN** o `CLAUDE.md` instrui a criar uma change com proposta, specs, design e tarefas antes de escrever código

### Requirement: Simplicidade como orientação aos agentes
O `CLAUDE.md` SHALL declarar simplicidade como princípio central: a solução mais simples que cumpre a spec vence; sem abstração especulativa, configuração sem uso ou generalização para casos que ainda não existem; port só quando há fornecedor externo a isolar. As regras de `design` em `openspec/config.yaml` SHALL exigir que cada decisão registre a alternativa mais simples considerada.

#### Scenario: Agente propõe uma abstração sem segundo uso
- **WHEN** um agente desenha uma interface para lógica interna com uma única implementação
- **THEN** o `CLAUDE.md` e as regras de design o orientam a escrever o código direto, sem a interface

### Requirement: README por componente sempre atualizado
O projeto pai e cada componente (`grolla-core`, `grolla-api`, `grolla-worker` e `e2e/fakes`) SHALL ter um `README.md` no padrão do README do eng-performance: cabeçalho centralizado com nome, frase de resumo em negrito e badges de stack e de harness; seções com emoji (o que é, features com ponto de entrada, arquitetura com diagrama e tabela, stack, como rodar, layout, gates, fluxo openspec, como contribuir, roadmap, licença), com o escopo de cada componente. Toda change SHALL revisar e atualizar os READMEs dos componentes que afetou e o README do pai antes de fechar. O `CLAUDE.md` SHALL conter essa regra.

#### Scenario: Change adiciona um endpoint
- **WHEN** uma change adiciona um endpoint ao `grolla-api`
- **THEN** antes do fechamento, a tabela de features do README do `grolla-api` e a do README do pai listam o endpoint

### Requirement: Artefatos do openspec são validados no build
O `make check` SHALL rodar `openspec validate --all --strict` e falhar se alguma change ou spec for inválida.

#### Scenario: Requisito sem cenário
- **WHEN** uma change contém um requisito sem cenário
- **THEN** `make check` falha no gate do openspec

### Requirement: Toda change fecha com os gates verdes
As regras de artefato em `openspec/config.yaml` SHALL exigir que as três últimas tarefas de toda change sejam revisar os READMEs afetados, rodar `make e2e` e rodar `make fmt && make check`, os dois últimos verdes.

#### Scenario: Gerar tarefas de uma change
- **WHEN** uma change nova gera o seu `tasks.md`
- **THEN** as três últimas tarefas são a revisão dos READMEs, o `make e2e` e o `make fmt && make check`

### Requirement: Versão única com bump por feature
A versão SHALL existir só no `pyproject.toml` do projeto pai. Cada change arquivada e mergeada SHALL incrementar o último dígito da versão.

#### Scenario: Feature entregue
- **WHEN** uma change é arquivada e mergeada na `master` com a versão 0.0.3
- **THEN** a versão passa a ser 0.0.4 num commit próprio
