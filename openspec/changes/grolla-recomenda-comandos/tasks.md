> Change multiagente: toca domínio e application (`grolla-dominio`), os adapters Anthropic e
> Kubernetes e o fake do LLM (`grolla-integracoes`), o Postgres (`grolla-persistencia`), o war room
> (`grolla-web`) e os cenários (`grolla-e2e`). Nenhum job do worker muda.

## 0. Contratos (agente pai)

- [x] 0.1 `Command`, `Action.commands`, `MAX_COMMANDS`, `MAX_COMMAND_CHARS` e a assinatura de `usable_commands` em `grolla.domain.analysis` (corpo provisório que só limpa e corta em 5).
- [x] 0.2 `RecordAction.command` e `RecordAction.run_in` em `grolla.domain.war_room`, gravados nos dados do `ACTION_RECORDED`; `record` do formulário lê `comando` e `onde`.
- [x] 0.3 `StepCommand` e `Step.commands` em `grolla_api.war_room`, com `Step.destructive` como property; o schema de `commands` registrado no `design.md`.
- [x] 0.4 Commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `usable_commands`: limpa espaços e crases em volta, descarta comando de mais de uma linha, com mais de 300 caracteres ou sem `run_in` (encadeado curto é aceito), e mantém os 5 primeiros na ordem; testes de cada regra.
- [ ] 1.2 `VERIFICATION_PROMPT`: regras de `commands` (passo a passo em ordem, cada um com `run_in`, encadear só o que roda no mesmo lugar e faz sentido junto, só nomes e valores vistos, sem `<placeholder>`, comando fora quando falta dado e `expected` dizendo o quê, lugares diferentes no mesmo passo) e os exemplos do design; testes do texto.
- [ ] 1.3 Comando aplicado: o item da timeline do post mortem e o card de encerramento ("Ações do time") mostram "Aplicou: <passo> — <comando> (<onde rodar>)"; o dado do rascunho do post mortem leva o comando e o lugar; testes.
- [ ] 1.4 `make check` verde no worktree.

## 2. grolla-integracoes

- [ ] 2.1 Adapter Anthropic: `commands` no schema da validação (em `required`), o `_Action` lê a lista e monta `Action.commands` com `usable_commands`; testes, incluindo comando descartado e as suítes de contrato.
- [ ] 2.2 `KubernetesClusterInventory`: toda resposta diz "no cluster <nome>", mesmo com um cluster só; testes.
- [ ] 2.3 `grolla-fakes`: o roteiro da validação confirmada devolve, na primeira ação, dois comandos em lugares diferentes (pipeline e cluster), no schema novo.
- [ ] 2.4 `make check` verde no worktree.

## 3. grolla-persistencia

- [ ] 3.1 `actions_data` grava `commands` e a leitura usa `a.get("commands", [])`; teste de integração com ação com e sem comandos e com linha antiga sem a chave.
- [ ] 3.2 `make check` verde no worktree.

## 4. grolla-web

- [ ] 4.1 `_step`: comandos da ação viram `StepCommand` com `run_in` e `destructive`; sem comandos, o primeiro trecho entre crases como hoje, sem `run_in`; testes.
- [ ] 4.2 Template do "Agora": "Para voltar ao normal" no primeiro passo com causa confirmada; em cada passo, a lista numerada de comandos com "Onde rodar: …", o bloco do comando e Copiar (confirmação no destrutivo, por comando); selo no passo quando algum comando é destrutivo; CSS no tema claro, no escuro e no celular.
- [ ] 4.3 Botão "Apliquei" em cada comando (formulário em `/actions/record` com `texto`, `comando` e `onde`, só com autor); o comando com um `ACTION_RECORDED` igual mostra "aplicado por <autor> às HH:MM"; a timeline do war room mostra "Aplicou: …"; testes.
- [ ] 4.4 Testes da página: passo com dois comandos em ordem, onde rodar de cada um, verificação antiga pelas crases.
- [ ] 4.5 `make check` verde no worktree.

## 5. grolla-e2e

- [x] 5.1 `causa_raiz.yaml` (ou cenário novo `comandos_no_plano.yaml`): o war room mostra "Para voltar ao normal", os dois comandos na ordem e o "Onde rodar" de cada um; "Apliquei" no primeiro comando, que passa a "aplicado por", com o item na timeline; ao resolver, o post mortem traz o comando aplicado na timeline.

## 6. Integração (agente pai)

- [ ] 6.1 Merge dos branches e conflitos resolvidos.
- [ ] 6.2 Code review do todo: consistência entre o schema do adapter, o fake, o `jsonb` e o `Step`; duplicação; desvio de contrato; simplicidade; cobertura das specs. Ajustes.
- [ ] 6.3 `docs/decisoes.md` e o roadmap.
- [ ] 6.4 Revisar e atualizar os READMEs do `grolla-core`, do `grolla-api`, do `e2e/fakes` e do pai (Features, Roadmap e badge de status).
- [ ] 6.5 `make e2e` com todos os cenários verdes.
- [ ] 6.6 `make fmt && make check` com tudo verde.
