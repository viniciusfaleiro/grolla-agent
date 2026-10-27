> Change multiagente: toca domínio, persistência, integrações, web e e2e. O agente pai escreve os contratos
> (`grolla.domain.safety`, campos novos, ports, migração) e faz o commit de contrato antes de abrir os agentes.
> Os jobs do worker (`run_replies`, `run_console_replies`) ficam com `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 `grolla.domain.safety` com `Scope`, `REFUSALS` e as assinaturas; `scope` em `ChatDraft`/`ConsoleDraft`; `refusal` nas mensagens; `MessageCounter` e `ensure_under_limit`; `RateLimitedError`; `PeriodMetrics.refused`; `investigation.chat_messages_per_hour` no config; migração das colunas `refusal`.
- [x] 0.2 Commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `is_destructive`, `looks_like_order` e `untrusted` com testes (casos de ataque e de uso legítimo).
- [x] 1.2 Toolbox embrulha todo resultado com `untrusted`; `_DATA_RULES` em todos os prompts; prompts neutros de domínio ("o ambiente em produção do time").
- [x] 1.3 Regra de escopo nos prompts do chat e do console; `run_next_reply` e `run_next_console_reply` trocam o texto pela recusa fixa e guardam `refusal`.
- [x] 1.4 `remember` grava `aprendido` e recusa `looks_like_order`.
- [x] 1.5 `ensure_under_limit` com `RateLimitedError`.
- [x] 1.6 `refused` nas métricas por período.
- [x] 1.7 Fakes atualizados (`grolla.testing`) e testes do caso de uso.

## 2. grolla-persistencia

- [x] 2.1 Colunas `refusal` lidas e gravadas; `PostgresMessageCounter.sent_since` nas duas tabelas; contagem de recusas por período; suítes de contrato.

## 3. grolla-integracoes

- [x] 3.1 Campo `scope` no schema das respostas do chat e do console no adapter do LLM; suíte de contrato.
- [x] 3.2 Roteiros do LLM fake com `scope` (`fora_do_escopo`, `pedido_de_execucao`) no `grolla-fakes`.
- [x] 3.3 Teste de capacidade (`tests/architecture/`): lista das ferramentas, métodos da nuvem e verbos HTTP do cluster.
- [x] 3.4 `evals/seguranca.py` e o alvo `make evals-seguranca` (pedido ao pai para o Makefile).

## 4. grolla-web

- [x] 4.1 Selo "destrutivo, confira antes" nos passos e no código das mensagens; copiar com confirmação.
- [x] 4.2 Recusa no chat e no console com um estilo próprio; aviso do limite por pessoa.
- [x] 4.3 "Pedidos recusados" em `/metricas`.
- [x] 4.4 Testes da api.

## 5. grolla-e2e

- [x] 5.1 Cenário `agente_recusa`: pergunta fora do domínio e pedido de execução recusados no console e no chat; nota-ordem não gravada; selo destrutivo no "Agora"; recusas em `/metricas`.

## 6. Fechamento (agente pai)

- [x] 6.1 Integração dos branches e code review do todo.
- [x] 6.2 `make evals-seguranca` com o modelo real, uma rodada, com o custo no relatório.
- [x] 6.3 Verificação no laboratório com o navegador headless.
- [x] 6.4 Registro de decisões, `deploy/` (rede interna até o S12) e READMEs do pai e dos componentes.
- [x] 6.5 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.6 Rodar `make fmt && make check` e garantir tudo verde.
