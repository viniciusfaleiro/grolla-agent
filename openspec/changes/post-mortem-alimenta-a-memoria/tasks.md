> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 `AgentEvaluation`, `PostMortem.evaluation`, `evaluated`; `EventType.SEEN_BEFORE_ANSWERED`; `domain.metrics` com `NotImplementedError`.
- [x] 0.2 `application.metrics` (`MetricsReader`, casos de uso); `move_status(repository=)`; `run_next_learning(post_mortems=)`; `LearningBrief.post_mortem`.
- [x] 0.3 Stubs no Postgres e nos fakes; migração 0010; containers com `metrics`.
- [x] 0.4 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `evaluated`, `periods` e `aggregate`.
- [x] 1.2 `agent_metrics`, `answer_seen_before` e `evaluate_agent`; `move_status` pede o aprendizado ao aprovar; `run_next_learning` com o post mortem aprovado (episódio confirmado e motivo "post mortem aprovado") e `learning_text` com o post mortem.
- [x] 1.3 Fakes (`InMemoryMetricsReader`) e suítes de contrato (`MetricsReaderContract`, `evaluation` no `PostMortemStoreContract`). Commit cedo.
- [x] 1.4 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0010 e `evaluation` no `PostgresPostMortemStore`.
- [x] 2.2 `PostgresMetricsReader` (resolvidos, aprovados com avaliação e a última resposta do "já vi isso" por incidente); suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 A avaliação no PDF; testes; `make check` verde.

## 4. grolla-web

- [x] 4.1 "Era o mesmo problema" e "Não era" na análise com "já vi isso", com a resposta registrada.
- [x] 4.2 Formulário da avaliação no post mortem.
- [x] 4.3 `/metricas` com semana ou mês e o link na barra; testes; README; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenário: responder "era o mesmo" e avaliar um post mortem aprovado; `/metricas` mostra o confirmado e o acerto.
- [x] 5.2 Cenário: aprovar o post mortem confirma o episódio na memória; `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches e resolver conflitos.
- [x] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [x] 6.3 Registro de decisões, roadmap e `CLAUDE.md`.
- [x] 6.4 Registrar no `design.md` os desvios de contrato.
- [x] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
