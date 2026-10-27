> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 `domain.triage`, eventos e kinds novos, `decide_merge` com `NotImplementedError`.
- [x] 0.2 `application.triage` (`TriageQueue`, `TriagePolicy`, casos de uso), `request_triage`, `TriageBrief`, `LanguageModel.triage`, `STAGE_TRIAGE`.
- [x] 0.3 Stubs no Postgres, no Anthropic e nos fakes; migração 0011; config `noise`; containers.
- [x] 0.4 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `needs_model`, `decide_triage` e `decide_merge`.
- [x] 1.2 `run_next_triage`, `reap_stuck_triages` e `merge_incidents`; `request_triage` quando o alerta abre o incidente e `noise` está ligado.
- [x] 1.3 `TRIAGE_PROMPT` e `triage_text`; fakes e suítes de contrato (commit cedo); jobs do worker; `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0011, `PostgresTriageQueue` e `request_triage`; suítes de contrato; README; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 Anthropic `triage`, com saída estruturada e sem ferramentas; cards no Teams; LLM fake com roteiro; `make check` verde.

## 4. grolla-web

- [x] 4.1 Aviso de sugestão com "Juntar" e a ação "Juntar com…" no war room; rótulos na timeline; testes; README; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenário: dois serviços, sugestão de junção, juntar.
- [x] 5.2 Cenário: alerta sem regra tem a severidade elevada pelo agente; `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches e resolver conflitos.
- [x] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [x] 6.3 Registro de decisões, roadmap e `CLAUDE.md`.
- [x] 6.4 Registrar no `design.md` os desvios de contrato.
- [x] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
