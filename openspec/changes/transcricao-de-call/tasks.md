> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 `domain.transcript`, eventos novos, `application.transcript`, `LanguageModel.extract_transcript`, `STAGE_TRANSCRIPT` com `NotImplementedError`.
- [x] 0.2 Stubs no Postgres, no Anthropic e nos fakes; migração 0013; config `transcript`; containers.
- [x] 0.3 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `clean_transcript` (`.vtt`/`.srt`/texto), `transcript_events` e a origem "transcrição" no `timeline_text`.
- [ ] 1.2 `upload_transcript`, `run_next_transcript` e `reap_stuck_transcripts`; `TRANSCRIPT_PROMPT` e `transcript_text`; fakes e suítes de contrato (commit cedo); jobs; `make check` verde.

## 2. grolla-persistencia

- [ ] 2.1 Migração 0013 e `PostgresTranscriptStore`; suítes de contrato; README; `make check` verde.

## 3. grolla-integracoes

- [ ] 3.1 `extract_transcript` no Anthropic (saída estruturada, sem ferramentas) e o LLM fake (itens a partir de linhas com "vamos", "fica com" e horários; hipótese descartada com "não foi"); `make check` verde.

## 4. grolla-web

- [ ] 4.1 Upload no war room (textarea ou arquivo) e itens com a origem na timeline; reprocessar uma transcrição que falhou; testes; README; `make check` verde.

## 5. grolla-e2e

- [ ] 5.1 Cenário: enviar o `.vtt` e ver a decisão, a ação e a hipótese descartada na timeline; o post mortem mostra a origem; `make check` verde.

## 6. Integração (agente pai)

- [ ] 6.1 Juntar os branches e resolver conflitos.
- [ ] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 6.3 Registro de decisões, roadmap e `CLAUDE.md`.
- [ ] 6.4 Registrar no `design.md` os desvios de contrato.
- [ ] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [ ] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
