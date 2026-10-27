> Change multiagente: domínio, persistência, integrações, web e e2e em paralelo a partir do commit de contrato. Os jobs do worker ficam com o `grolla-dominio`.

## 0. Contratos (agente pai)

- [x] 0.1 `domain.post_mortem` (seções, status, plano de ação, versões, draft e funções com `NotImplementedError`).
- [x] 0.2 `application.post_mortem` (`PostMortemStore`, `PostMortemJob`, filtros e resumo, `DocumentRenderer`, `PostMortemBrief`, casos de uso com `NotImplementedError`); `request_post_mortem`; `LanguageModel.draft_post_mortem`/`regenerate_section`; `STAGE_POST_MORTEM`.
- [x] 0.3 Stubs no Postgres, no Anthropic, no adapter `fpdf` e nos fakes; migração 0009; dependência `fpdf2`.
- [x] 0.4 Config `post_mortem` e etapa `post_mortem`; bootstrap (containers com `post_mortems` e `renderer`).
- [x] 0.5 `make check` verde e commit de contrato.

## 1. grolla-dominio

- [ ] 1.1 `metrics_text`, `timeline_text` (com a origem: alerta, agente, chat, pessoa), `drafted`, `edited`, `regenerated`, `moved` (regras de aprovar e encerrar) e `overdue`.
- [ ] 1.2 `run_next_post_mortem` (rascunho e regeneração; falha marca as seções), `reap_stuck_post_mortems`, `edit_section`, `request_regeneration`, `move_status`, `add_action`, `update_action`, `list_post_mortems` e `render_pdf`; `request_post_mortem` no `INCIDENT_RESOLVED`.
- [ ] 1.3 `POST_MORTEM_PROMPT` (blameless, fatores contribuintes, seções do template) e `post_mortem_text`.
- [ ] 1.4 Fakes e suítes de contrato (`PostMortemStoreContract`, `DocumentRendererContract`, as etapas do modelo). Commit cedo.
- [ ] 1.5 Jobs `run_post_mortems` e `reap_stuck_post_mortems`; pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [ ] 2.1 Migração 0009 e `PostgresPostMortemStore` (documento, versões, lista com filtros e vencidos, fila com `SKIP LOCKED`).
- [ ] 2.2 `request_post_mortem` no unit of work; suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [ ] 3.1 Anthropic: `draft_post_mortem` e `regenerate_section` com saída estruturada, sem ferramentas.
- [ ] 3.2 `grolla.adapters.fpdf.FpdfDocumentRenderer` (título, status, métricas, seções, plano de ação; texto em latin-1).
- [ ] 3.3 LLM fake com as duas etapas; suítes de contrato; READMEs; `make check` verde.

## 4. grolla-web

- [ ] 4.1 `/postmortems`, com filtros por status, severidade, serviço e período e com os itens vencidos.
- [ ] 4.2 `/postmortems/{incident_id}`:
  - seções renderizadas e edição por seção;
  - histórico de versões;
  - "regenerar seção" com instrução;
  - plano de ação (adicionar, editar, concluir, aceitar);
  - botões de status;
  - PDF.
- [ ] 4.3 Link "Post mortem" na página do incidente resolvido e na barra; testes; README; `make check` verde.

## 5. grolla-e2e

- [ ] 5.1 Cenário: resolver um incidente gera o rascunho com as seções e as métricas.
- [ ] 5.2 Cenário: editar move para em revisão; aprovar sem dono é recusado; com dono e prazo, aprova, e o PDF responde `application/pdf`.
- [ ] 5.3 Cenário: regenerar uma seção grava a versão anterior; `make check` verde.

## 6. Integração (agente pai)

- [ ] 6.1 Juntar os branches e resolver conflitos.
- [ ] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [ ] 6.3 Registro de decisões, roadmap e `CLAUDE.md` "Estado atual".
- [ ] 6.4 Registrar no `design.md` os desvios de contrato.
- [ ] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [ ] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [ ] 6.7 Rodar `make fmt && make check` e garantir tudo verde.
