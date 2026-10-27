> Change multiagente: toca domínio, persistência, integrações, web e e2e. O agente pai escreve os contratos
> (`grolla.domain.safety`, campos novos, ports, migração) e faz o commit de contrato antes de abrir os agentes.
> Os jobs do worker (`run_replies`, `run_console_replies`) ficam com `grolla-dominio`.

## 0. Contratos (agente pai)

- [ ] 0.1 `grolla.domain.safety` com `Scope`, `REFUSALS` e as assinaturas; `scope` em `ChatDraft`/`ConsoleDraft`; `refusal` nas mensagens; `count_by_author_since` nos ports; `RateLimitedError`; `PeriodMetrics.refused`; `chat.max_messages_per_hour` no config; migração das colunas `refusal`.
- [ ] 0.2 Commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `is_destructive`, `looks_like_order` e `untrusted` com testes (casos de ataque e de uso legítimo).
- [ ] 1.2 Toolbox embrulha todo resultado com `untrusted`; `_DATA_RULES` em todos os prompts; prompts neutros de domínio ("o ambiente em produção do time").
- [ ] 1.3 Regra de escopo nos prompts do chat e do console; `run_next_reply` e `run_next_console_reply` trocam o texto pela recusa fixa e guardam `refusal`.
- [ ] 1.4 `remember` grava `aprendido` e recusa `looks_like_order`.
- [ ] 1.5 Limite por autor em `ask_chat`/`ask_console` com `RateLimitedError`.
- [ ] 1.6 `refused` nas métricas por período.
- [ ] 1.7 Fakes atualizados (`grolla.testing`) e testes do caso de uso.

## 2. grolla-persistencia

- [ ] 2.1 Colunas `refusal` lidas e gravadas; `count_by_author_since` nas duas tabelas; contagem de recusas por período; suítes de contrato.

## 3. grolla-integracoes

- [ ] 3.1 Campo `scope` no schema das respostas do chat e do console no adapter do LLM; suíte de contrato.
- [ ] 3.2 Roteiros do LLM fake com `scope` (`fora_do_escopo`, `pedido_de_execucao`) no `grolla-fakes`.
- [ ] 3.3 Teste de capacidade (`tests/architecture/`): lista das ferramentas, métodos da nuvem e verbos HTTP do cluster.
- [ ] 3.4 `evals/seguranca.py` e o alvo `make evals-seguranca` (pedido ao pai para o Makefile).

## 4. grolla-web

- [ ] 4.1 Selo "destrutivo, confira antes" nos passos e no código das mensagens; copiar com confirmação.
- [ ] 4.2 Recusa no chat e no console com um estilo próprio; aviso do limite por pessoa.
- [ ] 4.3 "Pedidos recusados" em `/metricas`.
- [ ] 4.4 Testes da api.

## 5. grolla-e2e

- [ ] 5.1 Cenário `agente_recusa`: pergunta fora do domínio e pedido de execução recusados no console e no chat; nota-ordem não gravada; selo destrutivo no "Agora"; recusas em `/metricas`.

## 6. Fechamento (agente pai)

- [ ] 6.1 Integração dos branches e code review do todo.
- [ ] 6.2 `make evals-seguranca` com o modelo real, uma rodada, com o custo no relatório.
- [ ] 6.3 Verificação no laboratório com o navegador headless.
- [ ] 6.4 Registro de decisões, `deploy/` (rede interna até o S12) e READMEs do pai e dos componentes.
- [ ] 6.5 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 6.6 Rodar `make fmt && make check` e garantir tudo verde.
