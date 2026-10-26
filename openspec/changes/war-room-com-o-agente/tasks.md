> Change multiagente: domínio, persistência, integrações, web e e2e trabalham em paralelo a partir do commit de contrato.

## 0. Contratos (agente pai)

- [x] 0.1 Modelos, ações, estados, eventos e kinds novos no domínio, com as assinaturas de `decide_action`.
- [x] 0.2 Application: `act`, `ask`, `ChatQueue`, `run_next_reply`, `reap_stuck_replies`, `chat_context`, `LanguageModel.reply`, `incident_for_update` e `chat_of`, com corpos `NotImplementedError`.
- [x] 0.3 Stubs no Postgres, no adapter da Anthropic e nos fakes; migração `0005`.
- [x] 0.4 `bootstrap`: limites do chat, a etapa `warroom_chat` e o `WorkerContainer.chat_queue`. HTMX como estático na api.
- [x] 0.5 `make check` verde com os stubs e commit de contrato.

## 1. grolla-dominio

- [x] 1.1 `decide_action`: todas as ações, transições válidas e inválidas, horários, eventos com autor e cards.
- [x] 1.2 Agrupamento ignorando incidentes fora de `OPEN_STATES`.
- [x] 1.3 `act` e `ask`, e `run_next_reply`/`reap_stuck_replies` (limites, teto por incidente, resposta sempre chega), `chat_context` e `CHAT_PROMPT`.
- [x] 1.4 Fakes e suítes de contrato do `ChatQueue`, de `incident_for_update`, de `chat_of` e de `LanguageModel.reply`. Commit cedo.
- [x] 1.5 Pisos de cobertura e `make check` verde.

## 2. grolla-persistencia

- [x] 2.1 Migração 0005, os campos novos na projeção e `incident_for_update` com `FOR UPDATE`.
- [x] 2.2 `chat_messages`, `ChatQueue` com `SKIP LOCKED` e `chat_of`. `active_incidents` só com estados abertos.
- [x] 2.3 Integração passando nas suítes de contrato; README do core; `make check` verde.

## 3. grolla-integracoes

- [x] 3.1 `reply` no adapter da Anthropic (laço de ferramentas, resposta em texto, etapa `warroom_chat`) e o modo de chat no LLM fake.
- [x] 3.2 Cards: IC assumido, severidade alterada por humano, mitigado, encerrado (duração, autor, motivo, causa raiz e ações) e descartado.
- [x] 3.3 Jobs `run_replies` e `reap_stuck_replies` no worker; READMEs; `make check` verde.

## 4. grolla-web

- [x] 4.1 War room:
  - cabeçalho com estado, severidade, duração, IC e TTA, TTM e TTR;
  - ações permitidas no estado atual;
  - chat;
  - timeline com autor;
  - polling HTMX;
  - cookie do autor.
- [x] 4.2 Rotas de ação e de chat, com erros de ação inválida mostrados na página; testes; README; `make check` verde.

## 5. grolla-e2e

- [x] 5.1 Cenários de assumir, mitigar e encerrar com cards. Ação inválida recusada.
- [x] 5.2 Chat com resposta e consulta registrada, chat com LLM fora do ar e alerta depois do encerramento abrindo incidente novo. `make check` verde.

## 6. Integração (agente pai)

- [x] 6.1 Juntar os branches e resolver conflitos.
- [x] 6.2 Fazer o code review do todo entregue, com os ajustes que couberem.
- [x] 6.3 Laboratório:
  - repetir o cenário "banco fora";
  - perguntar no chat "o banco já voltou?" depois de religar o RDS;
  - assumir, mitigar e encerrar pelo war room.
- [x] 6.4 Registrar no `design.md` os desvios de contrato.
- [x] 6.5 Revisar e atualizar os READMEs do pai e de cada componente.
- [x] 6.6 Rodar `make e2e` e garantir todos os cenários verdes.
- [x] 6.7 Rodar `make fmt && make check` e garantir tudo verde.

## 7. Revisão de UX e design (agente pai, só `packages/grolla-api/`)

- [x] 7.1 Tokens de design (cores, tipografia, tema escuro), o mascote como avatar e favicon, e a página de lista de incidentes.
- [x] 7.2 War room: cabeçalho com pulso e ciclo de estados, ação principal em destaque, seletor de severidade e popovers de encerrar e descartar; descarte de hipótese na análise.
- [x] 7.3 Chat com cara de chat: bolhas por autor, Grolla com avatar, markdown mínimo seguro, consultas recolhidas, indicador "investigando…", sugestões com um clique, Enter envia e rolagem automática.
- [x] 7.4 Timeline legível: trilho vertical, sem eventos de enfileiramento de card.
- [x] 7.5 Chat responde com o que já consultou quando o orçamento da resposta está acabando (última chamada sem ferramentas).
- [x] 7.6 Prompt do chat com resposta curta e direta: formato fixo, até 3 evidências, cerca de 60 palavras, só as consultas necessárias.
- [x] 7.7 Testes da web atualizados, README do `grolla-api`, `make e2e` e `make fmt && make check` verdes.
