> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [ ] 0.1 `domain.console`, `decide_manual` e `INCIDENT_OPENED_MANUALLY` com `NotImplementedError`.
- [ ] 0.2 `application.console` (`ConsoleStore`, `ConsolePolicy` e casos de uso), `Telemetry.series`, `LanguageModel.consult`, `STAGE_CONSOLE`, `CONSOLE_PROMPT`/`console_text`.
- [ ] 0.3 Stubs no Postgres, no SigNoz, no Anthropic e nos fakes; migração 0012; config `console`; containers e a fila `console` no worker.
- [ ] 0.4 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `session_title`, `valid_charts` e `decide_manual`.
- [ ] 1.2 `ask_console`, `run_next_console_reply` (teto por resposta e diário, `remember` com o autor, falha vira "não consegui responder"), `reap_stuck_console_replies`, `open_incident_from_console` e `chart_series`.
- [ ] 1.3 `CONSOLE_PROMPT`, no formato curto do chat e com a regra dos gráficos, e `console_text`; fakes e suítes de contrato (commit cedo); job `run_console_replies` na fila `console`; `make check` verde.

## 2. grolla-persistencia

- [ ] 2.1 Migração 0012 e `PostgresConsoleStore` (sessões, mensagens, fila com `SKIP LOCKED` e tokens do dia); suítes de contrato; README; `make check` verde.

## 3. grolla-integracoes

- [ ] 3.1 `SignozTelemetry.series` e no fake do SigNoz; `consult` no Anthropic (saída com `text` e `charts`); LLM fake com a etapa `console`; `make check` verde.

## 4. grolla-web

- [ ] 4.1 `/console` (sessões e nova conversa) e `/console/{sessao}` (chat com polling, gráficos SVG desenhados com `chart_series`, "abrir incidente"); link na barra; testes; README; `make check` verde.

## 5. grolla-e2e

- [ ] 5.1 Cenário: pergunta no console, resposta e gráfico desenhado.
- [ ] 5.2 Cenário: abrir incidente a partir da conversa (análise pedida e link na sessão).
- [ ] 5.3 Cenário: nota ensinada pelo console; `make check` verde.

## 6. Integração (agente pai)

- [ ] 6.1 Juntar os branches e resolver conflitos.
- [ ] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 6.3 Registro de decisões, roadmap e `CLAUDE.md`.
- [ ] 6.4 Registrar no `design.md` os desvios de contrato.
- [ ] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [ ] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
