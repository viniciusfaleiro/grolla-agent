> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato. Os jobs do worker ficam com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 `domain.follow_up` (Trend, Proposal, CycleDraft, Cycle, CycleStatus, FollowUpPolicy, CycleDecision, funções com `NotImplementedError`); eventos e kinds novos; `Notification.cycle`/`impact`.
- [x] 0.2 `application.follow_up` (CycleCandidate, CycleQueue, ImpactPolicy, casos de uso com `NotImplementedError`); `request_cycle`; `cycles_of`; `LanguageModel.follow_up`, `STAGE_FOLLOW_UP`, `FOLLOW_UP_PROMPT`/`follow_up_text`.
- [x] 0.3 Stubs no Postgres, no Anthropic e nos fakes; migração 0008.
- [x] 0.4 Config `follow_up`, `impact` e etapa `follow_up`; bootstrap (containers com `cycle_queue`, `follow_up_policy` e `impact_policy`); `e2e/config.yaml` e `deploy/credenciadora.yaml`.
- [x] 0.5 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `cycle_due`, `completed`, `partial_cycle`, `decide_cycle` (postar quando muda ou após `quiet`, proposta após `stable` normalizado e uma vez por estado, parada no `max_cycles`) e `estimated_impact`.
- [x] 1.2 `decide_firing`: mitigado volta para em tratamento quando o alerta dispara.
- [x] 1.3 `schedule_cycles`, `run_next_cycle` (contexto do chat, ciclo anterior, memória e embedder, teto de tokens do incidente, falha vira parcial), `reap_stuck_cycles` e `impact_of`.
- [x] 1.4 `request_cycle` no alerta agrupado e no `ask`.
- [x] 1.5 `FOLLOW_UP_PROMPT`, curto, no estilo do chat, e `follow_up_text`.
- [x] 1.6 Fakes (`InMemoryCycleQueue`, `ScriptedLanguageModel.follow_up`) e suítes de contrato (`CycleQueueContract`, `cycles_of`, `request_cycle`, `follow_up` do modelo). Commit cedo.
- [x] 1.7 Jobs `schedule_cycles`, `run_cycles` e `reap_stuck_cycles` no worker, só com `follow_up`; pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0008 (`cycles`, `follow_ups`).
- [x] 2.2 `PostgresCycleQueue` (`candidates`, `enqueue` idempotente, `claim` com `SKIP LOCKED`, `stuck`, `finish` em transação) e o uso com a etapa `follow_up`.
- [x] 2.3 `request_cycle` no unit of work e `cycles_of` no repositório; suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 Anthropic: `follow_up` com saída estruturada (tendência, taxa de erro, resumo, mudou, evidências) e fechamento no orçamento.
- [x] 3.2 Teams: cards "Atualização" (com tendência, taxa de erro, resumo e impacto), "Sem mudança", "Grolla sugere…" e "Alerta voltou a disparar".
- [x] 3.3 LLM fake: etapa `follow_up` com roteiro (padrão `normalizado`, 0% de erro). Suítes de contrato; READMEs do core e do `e2e/fakes`; `make check` verde.

## 4. grolla-web

- [x] 4.1 War room: seção "Acompanhamento" com os últimos ciclos (tendência, taxa de erro, resumo, horário) e o impacto estimado no cabeçalho.
- [x] 4.2 Aviso "Grolla sugere marcar como mitigado/encerrar", com o motivo e o botão da ação, enquanto a proposta vale para o estado atual.
- [x] 4.3 Timeline com os eventos novos; testes; README do `grolla-api`; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenário: um P1 recebe ciclo, com evento na timeline e card "Atualização".
- [x] 5.2 Cenário: ciclos `normalizado` por `stable_minutes` geram a proposta de mitigar (evento, card e aviso na página).
- [x] 5.3 Cenário: mitigado volta para em tratamento quando o alerta dispara de novo; `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches e resolver conflitos.
- [x] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [x] 6.3 Registro de decisões, roadmap e `CLAUDE.md` "Estado atual".
- [x] 6.4 Registrar no `design.md` os desvios de contrato.
- [x] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
