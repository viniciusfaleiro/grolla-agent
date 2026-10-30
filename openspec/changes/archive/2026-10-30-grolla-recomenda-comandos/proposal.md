## Why

Com a causa confirmada, o time quer o comando que resolve e o lugar certo para rodá-lo. Hoje o
plano de ação é texto livre: o war room só mostra um comando quando o modelo, por acaso, escreve
um trecho entre crases, e nunca diz em qual cluster, namespace, conta ou região rodar. O design
do war room ("Para voltar ao normal", com o comando exato e o botão Copiar) pede o comando pronto,
por exemplo `kubectl scale deploy/antifraude-api --replicas=2 -n loja`. Errar o lugar é o erro mais
caro de um comando certo: por isso o "onde executar" vem junto e explícito.

## What Changes

- Cada ação do plano da validação ganha `commands`, um passo a passo de até 5 comandos, em
  ordem. Cada comando tem:
  - `command`: o comando exato. Pode ser de qualquer ferramenta que o time usa (`kubectl`,
    `aws`, `helm`, `az`, a CLI de um banco), e só usa nomes e valores que as ferramentas
    mostraram (namespace, workload, réplicas anteriores, revisão, build, recurso), sem nada
    inventado nem `<placeholder>`;
  - `run_in`: onde executar, obrigatório. Por exemplo, "cluster loja-prod, namespace loja",
    "conta AWS producao (851725637288), região us-east-1" ou "Azure DevOps, pipeline
    estoque-api-cd". Comandos do mesmo passo podem rodar em lugares diferentes, como o rollback
    no pipeline e depois o reinício no cluster.
- Sem um dado necessário, o comando que dependeria dele fica de fora, e o `expected` diz o que
  falta. O modelo nunca chuta um comando.
- Um comando pode encadear outros (`&&`, `|`, `;`) quando eles rodam no mesmo lugar e fazem
  sentido juntos, como `kubectl rollout undo deploy/pedidos-api -n loja && kubectl rollout
  status deploy/pedidos-api -n loja`. Lugares diferentes ou passos independentes viram comandos
  separados no passo a passo.
- O núcleo descarta, um a um, os comandos que têm mais de uma linha, passam de 300 caracteres
  ou vêm sem `run_in`. Também corta a lista em 5. A ação continua no plano com os comandos que
  sobraram.
- No war room, cada passo do plano mostra os seus comandos numerados, cada um com "Onde rodar:
  …" e o botão Copiar. O primeiro passo vem em destaque como "Para voltar ao normal", conforme o design. O selo de
  destrutivo e a confirmação ao copiar continuam valendo para todo comando.
- Cada comando ganha o botão "Apliquei". O clique registra, com o autor e o horário, que a
  pessoa rodou aquele comando naquele lugar, sem formulário. O comando passa a mostrar
  "aplicado por Ana às 15:31". O registro entra na linha do tempo, no card de encerramento
  ("Ações do time") e no post mortem, na timeline e no dado do rascunho. É o mesmo evento de
  "registrar uma ação feita", com o comando e o lugar como dados a mais.
- O chat do war room e o console ganham a mesma capacidade de tirar o time da crise. Quando
  sugerem comandos, a resposta traz o passo a passo, cada comando com onde rodar, pelas mesmas
  regras, desenhado como no plano: bloco escuro, Copiar e, no war room, "Apliquei". O prompt
  deixa de limitar a uma sugestão só, passa a dizer que o objetivo é resolver o incidente o mais
  rápido possível e pede ao time a evidência que o Grolla não alcança (o que colar e onde
  conseguir).
- No war room, o plano fica recolhido numa barra "O que o Grolla faria?", fechada de início,
  com o número de passos e de comandos. Aberta, mostra o passo a passo.
- Verificações antigas, sem os campos novos, continuam mostrando o comando tirado das crases,
  como hoje.
- O agente continua somente leitura: o comando é sugestão para o time rodar, e nenhuma
  ferramenta nova de execução entra no Grolla.

## Non-goals

- Comandos no card "Causa raiz" do Teams e no plano do post mortem. Ficam como hoje, e o link
  do war room leva ao comando.
- Separar no plano os passos "para voltar ao normal" dos passos "para não voltar". Só o
  primeiro passo ganha o título do design. O resto segue em "Depois: mais N passos".
- Executar os comandos, preencher com credenciais ou gerar um script que rode o passo a passo
  sozinho.
- Evals novos de comando com o modelo real, que gastam crédito. Ficam para quando houver dados
  do uso.

## Capabilities

### New Capabilities

Nenhuma.

### Modified Capabilities

- `root-cause-verification`: cada ação do plano pode trazer um passo a passo de comandos, cada
  um com onde executá-lo, só com nomes vistos nas ferramentas, e o núcleo descarta comando inválido.
- `incident-pages`: o war room mostra os comandos de cada passo, numerados, com o "onde rodar"
  e o botão Copiar, com o primeiro como "Para voltar ao normal".
- `war-room-actions`: o botão "Apliquei" registra o comando aplicado, com o passo e o lugar.
- `post-mortem`: os comandos aplicados entram na timeline, no rascunho e no card de
  encerramento.
- `war-room-chat`: a resposta pode trazer ações e o passo a passo de comandos, pede a evidência
  que falta e tem como objetivo resolver rápido.
- `operations-console`: a resposta pode trazer o passo a passo de comandos e pede a evidência
  que falta.
- `cluster-investigation`: toda resposta das ferramentas do cluster diz de que cluster veio,
  mesmo com um cluster só.

## Impact

- `grolla.domain.analysis`: `Command(command, run_in)`, `Action.commands` e a regra que descarta
  comando inválido.
- `grolla.application.investigation`: regras do `VERIFICATION_PROMPT` sobre o passo a passo de
  comandos e onde rodar cada um.
- `grolla.adapters.kubernetes`: toda resposta diz o nome do cluster, mesmo com um cluster só.
- `grolla.adapters.anthropic`: o schema da validação ganha `commands`.
- `grolla.adapters.postgres`: `commands` no `jsonb` das ações, sem migração. Linha antiga sem
  ele lê uma lista vazia.
- `grolla.domain.war_room`: `RecordAction` aceita o comando e o lugar. `grolla.domain.post_mortem`
  e o card de encerramento mostram os dois.
- `grolla.domain.chat` e `grolla.domain.console`: `commands` no rascunho e na mensagem do
  agente. `CHAT_PROMPT` e `CONSOLE_PROMPT` com as regras novas.
- `grolla.adapters.postgres`: migração 0015 com `commands jsonb` em `chat_messages` e
  `console_messages`.
- `grolla-api`: `Step` com os comandos vindos da ação, o botão "Apliquei", a marca de aplicado, o
  template do "Agora" e o CSS.
- `e2e/fakes`: o roteiro da validação devolve um passo com dois comandos em lugares diferentes.
  Um cenário confere o war room.
- Sem port novo, sem tipo de evento novo, sem endpoint novo (a única migração é a 0015): "Apliquei" usa a rota de registrar
  ação que já existe.
