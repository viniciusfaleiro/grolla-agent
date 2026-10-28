## Context

O plano da validação é uma lista de `Action(action, target, expected, link)`. O war room
(`grolla_api.war_room._step`) procura o primeiro trecho entre crases de `action` e o mostra como
comando copiável, com o selo de destrutivo (`is_destructive`). Na prática o comando só aparece
quando o modelo decide escrever crases, nunca diz onde rodar e pode vir pela metade.

O que o modelo sabe para montar um comando vem das ferramentas:
- `cluster_state` e `cluster_changes` dão namespace, workload, réplicas, revisões e o valor
  anterior das mudanças (quando `show_values` deixa). O nome do cluster só aparece quando há mais
  de um cluster ligado.
- As ferramentas `cloud_*` dão o recurso, a região e o nome da conta (nas contas extras).
- `recent_deploys` dá o build e o pipeline.

O design do war room mostra o passo "Para voltar ao normal" com o comando exato, o botão Copiar e
o efeito esperado.

## Goals / Non-Goals

**Goals:**
- Cada ação do plano pode trazer um passo a passo de comandos, cada um com onde rodá-lo, de
  qualquer ferramenta, só com dados vistos.
- O núcleo recusa formatos perigosos ou ambíguos de comando sem perder a ação.
- O war room mostra os comandos numerados de cada passo, com onde rodar e Copiar, conforme o
  design.

**Non-Goals:** os da proposta (Teams, post mortem, chat e console, separar os passos por tipo,
executar, evals com o modelo real).

## Decisions

### 1. Um passo a passo de comandos, cada um com o seu lugar

`Action` ganha `commands: tuple[Command, ...] = ()`, e cada `Command` tem `command` (o comando
exato) e `run_in` (onde rodar). A lista é ordenada e tem até 5 comandos. O modelo a preenche pela
saída estruturada. Comandos de um mesmo passo podem rodar em lugares diferentes. Exemplo de
rollback: reimplantar o build anterior no pipeline e depois acompanhar o rollout no cluster.

- *Alternativa mais simples:* manter as crases e só pedir no prompt "ponha os comandos entre
  crases e diga onde rodar". Foi descartada porque o lugar viraria texto solto, sem garantia de
  vir junto com cada comando, e o war room continuaria adivinhando o que é comando.
- *Alternativa considerada:* um `command`/`run_in` só por ação, com o passo a passo feito de
  várias ações. Foi descartada porque o time pensa em "o que fazer" (a ação, com o efeito
  esperado) e em "como fazer" (os comandos). Partir uma ação em várias repetiria o `expected` e
  bagunçaria a ordem do plano.
- `run_in` é texto livre ("cluster loja-prod, namespace loja"), não um objeto com `cluster`,
  `namespace`, `account` e `region`. Os lugares variam por ferramenta (cluster, conta, pipeline,
  banco), e o texto basta para quem vai rodar. Um objeto exigiria um schema por tipo de lugar,
  sem segundo uso hoje.

### 2. O núcleo descarta, um a um, os comandos que não cabem

`grolla.domain.analysis.usable_commands(raw: Iterable[tuple[str, str]]) -> tuple[Command, ...]`
recebe os pares (comando, onde rodar) do modelo. Cada comando é limpo (espaços nas pontas e
crases em volta) e descartado quando:
- tem mais de uma linha, ou passa de 300 caracteres;
- vem sem `run_in`.

Dos que sobram, ficam os 5 primeiros, na ordem. O adapter aplica a regra ao montar o
`VerificationDraft`.

Encadear (`&&`, `|`, `;`) é permitido, porque às vezes faz sentido juntar o que roda no mesmo
lugar (`kubectl rollout undo … && kubectl rollout status …`). O limite de 300 caracteres numa
linha mantém o comando legível para quem confere antes de rodar. O selo de destrutivo já usa
`is_destructive` na linha inteira, então um `delete` no meio de um encadeamento continua marcado.

- *Alternativa considerada:* recusar todo encadeamento e obrigar um comando por item. Foi
  descartada a pedido do usuário: juntar dois comandos curtos do mesmo lugar é mais natural para
  quem vai rodar, e a lista continua servindo para lugares diferentes.

- *Alternativa descartada:* recusar a validação inteira (`NotConcreteError`) e pedir correção,
  como no passo de investigação. Uma correção custa uma volta inteira do modelo, e a ação com os
  outros comandos continua útil.

### 3. O prompt pede o comando só com dados vistos

Regras novas no `VERIFICATION_PROMPT`, na parte de `actions`:
- `commands`: o passo a passo que faz a ação, em ordem, um comando por item, na ferramenta do
  time (`kubectl`, `aws`, `helm`, `az` e outras), com os nomes e valores que as ferramentas
  mostraram. Pode misturar lugares, por exemplo o pipeline e depois o cluster. Pode encadear
  (`&&`, `|`) comandos curtos que rodam no mesmo lugar e fazem sentido juntos. Lugares
  diferentes ou passos independentes vão em itens separados.
- `run_in` de cada comando: onde rodar, com o nome do cluster e o namespace, ou a conta e a
  região, ou o pipeline.
- Sem um dado necessário, o comando que dependeria dele fica de fora, e `expected` diz o que
  falta. Nunca `<placeholder>`, nunca valor chutado. Sem nenhum comando possível, a lista fica
  vazia.
- Exemplos: `kubectl scale deploy/antifraude-api --replicas=2 -n loja` no "cluster loja-prod,
  namespace loja"; `aws rds start-db-instance --db-instance-identifier grolla-lab-db --region
  us-east-1` na "conta AWS producao, região us-east-1".

O gate de vocabulário proíbe nomes de fornecedor na application, e isso vale para o prompt. Os
exemplos do prompt descrevem o comando sem citar a ferramenta ("o comando da CLI do cluster que
escala o deployment antifraude-api para 2 réplicas no namespace loja"). O modelo conhece as CLIs
e escreve o comando real. Os exemplos com `kubectl` e `aws` ficam nos testes do adapter, no fake e
nesta change.

As regras de dados externos (`_DATA_RULES`) continuam valendo. Uma ordem vinda de um log nunca
vira comando.

### 4. O cluster sempre diz o nome

`KubernetesClusterInventory` passa a rotular cada item com "no cluster <nome>" mesmo com um
cluster só. Hoje o rótulo só aparece com vários (`labeled=many`). Sem o nome, o modelo não tem
como dizer onde rodar.

- *Alternativa considerada:* pôr os nomes dos clusters e das contas configurados no texto da
  validação (`verification_text`). Foi descartada porque o brief teria de carregar a configuração
  de infraestrutura para dentro da application, e o nome que importa é o do lugar onde a
  ferramenta realmente achou o recurso.

Na AWS nada muda. A região já vem em todo item, e o nome da conta vem nas contas extras. Na conta
da instância, o modelo escreve "conta da instância, região us-east-1".

### 5. Persistência sem migração

`actions_data` grava `commands` (lista de `{command, run_in}`) no `jsonb` das ações. A leitura usa
`a.get("commands", [])`, então linhas antigas leem uma lista vazia.

### 6. War room: os comandos vêm da ação; as crases ficam para o que é antigo

`Step` passa a ter `commands: tuple[StepCommand, ...]`, e cada `StepCommand` tem `command`,
`run_in` e `destructive`. Em `_step`, uma ação com comandos usa esses comandos. Sem eles, vale o
primeiro trecho entre crases, como hoje: um `StepCommand` sem `run_in`, para verificações antigas
e para o plano da análise inicial, que é texto. O template mostra, em cada passo com comandos, a
lista numerada. Cada item tem:
- o bloco escuro com o comando e o botão Copiar (o `data-confirm` do destrutivo segue igual, por
  comando);
- "Onde rodar: …" logo acima do comando.

O selo de destrutivo do passo aparece quando qualquer comando dele é destrutivo. O primeiro passo,
com a causa confirmada, ganha o título "Para voltar ao normal" no lugar de "Comece por aqui". O
plano da análise inicial mantém "O Grolla investigaria primeiro".

### 7. "Apliquei" é o registro de ação que já existe, com dois dados a mais

O war room já tem "registrar o que você fez": `RecordAction(text)` grava `ACTION_RECORDED`, a
timeline mostra o evento, o card de encerramento lista as ações em "Ações do time" e o post
mortem leva o evento na timeline. "Apliquei" reaproveita tudo isso:
- `RecordAction` ganha `command: str | None = None` e `run_in: str | None = None`. Com comando,
  o evento grava `{"text": <passo>, "command": …, "run_in": …}`.
- O botão é um formulário que posta em `/incidents/{id}/actions/record`, com `texto` (o passo),
  `comando` e `onde` escondidos. Sem rota nova.
- A timeline do war room, a do post mortem (`_DETAIL_KEYS` e o texto do item) e o card de
  encerramento mostram "Aplicou: <passo> — <comando> (<onde rodar>)" quando há comando.
- O war room marca como aplicado o comando do plano que tem um `ACTION_RECORDED` com o mesmo
  `command`, e mostra o autor e o horário do último registro.

- *Alternativa considerada:* um evento novo `COMMAND_APPLIED`. Foi descartada porque é o mesmo
  fato, "a pessoa fez isto", e um tipo novo exigiria mexer em cada lugar que já trata
  `ACTION_RECORDED` (timeline, origem, post mortem, card) para dar o mesmo resultado.
- *Alternativa considerada:* deixar a pessoa usar o "Registrar" de texto livre. Foi descartada
  porque ninguém redigita o comando e o lugar durante um incidente, e sem o comando exato o war
  room não sabe o que já foi aplicado.

## Contratos

Change multiagente. O commit de contrato do agente pai traz:

- **Domínio** (`grolla/domain/analysis.py`):
  ```python
  @dataclass(frozen=True)
  class Command:
      """Um comando do passo a passo de uma ação; o agente não executa."""

      command: str  # exato, numa linha
      run_in: str  # onde rodar: cluster e namespace, conta e região, pipeline


  @dataclass(frozen=True)
  class Action:
      action: str
      target: str
      expected: str
      link: str | None
      commands: tuple[Command, ...] = ()  # em ordem; vazio quando não deu para montar


  MAX_COMMANDS = 5
  MAX_COMMAND_CHARS = 300


  def usable_commands(raw: Iterable[tuple[str, str]]) -> tuple[Command, ...]: ...
  ```
- **Schema da validação** (adapter Anthropic e fake): cada item de `actions` ganha
  `"commands": {"type": "array", "items": {"type": "object", "properties": {"command":
  {"type": "string"}, "run_in": {"type": "string"}}, "required": ["command", "run_in"],
  "additionalProperties": false}}`, em `required` (lista vazia quando não há comando).
- **jsonb `verifications.actions`:** chave `commands`, lista de `{"command", "run_in"}`, opcional
  na leitura.
- **Web:** `StepCommand(command, run_in: str | None, destructive)` e `Step(what, target, expected,
  link, commands: tuple[StepCommand, ...])`; `Step.destructive` passa a ser uma property
  (qualquer comando destrutivo).
- **War room** (`grolla/domain/war_room.py`): `RecordAction(text: str, command: str | None = None,
  run_in: str | None = None)`; dados do `ACTION_RECORDED` com `command` e `run_in` opcionais.
- **Formulário** de `/incidents/{id}/actions/record`: campos `texto`, `comando` e `onde`, os dois
  últimos opcionais.
- **Sem** port, tipo de evento, endpoint ou migração novos.

## Risks / Trade-offs

- [O modelo inventa um nome que parece real] → O prompt exige nomes vistos. O `run_in` deixa
  explícito onde o comando vai rodar, e quem roda confere. O selo de destrutivo e a confirmação
  ao copiar continuam. Não há checagem automática de que cada nome apareceu numa avaliação: ela
  daria falso negativo com valores derivados, como "2 réplicas antes".
- [Comando certo no lugar errado] → `run_in` é obrigatório em cada comando, e o cluster passa a
  vir sempre nomeado nas ferramentas.
- [Valores de ConfigMap em cluster PCI] → Com `show_values: false`, o modelo não vê o valor
  anterior. Pela regra 3, o comando de reverter fica de fora e o `expected` diz que falta o valor.
- [Mais tokens de saída na validação] → Até 5 linhas curtas por ação, pouco perto do orçamento
  da etapa.

## Migration Plan

Sem migração. Validações novas trazem os campos e validações antigas seguem pelas crases. O
rollback é voltar o commit: os campos a mais no `jsonb` são ignorados pela versão anterior.

## Open Questions

Nenhuma.
