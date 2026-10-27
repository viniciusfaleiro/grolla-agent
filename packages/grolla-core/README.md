<div align="center">

<img src="../../docs/assets/grolla.svg" alt="Grolla" width="96">

# grolla-core

**O coração do agent-grolla: domínio, casos de uso, ports com nome de capacidade e os adapters que isolam cada fornecedor.**

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Kind](https://img.shields.io/badge/tipo-biblioteca-lightgrey)](#-o-que-é)
[![Architecture](https://img.shields.io/badge/arquitetura-hexagonal-blueviolet)](#-arquitetura)
[![Coverage floor](https://img.shields.io/badge/cobertura-domain%2090%2F80%20%C2%B7%20application%2080%2F70-success)](#-testes)
[![Contracts](https://img.shields.io/badge/ports-su%C3%ADte%20de%20contrato-25A162)](#-testes)

</div>

---

## ✨ O que é

Biblioteca compartilhada pelos deployables (`grolla-api` e `grolla-worker`). Não roda sozinha.
Tudo o que é regra de negócio, contrato com o mundo externo e acesso a infraestrutura mora aqui;
os deployables só montam o processo.

## 🚀 Features

| Área | O que entrega | Ponto de entrada |
|---|---|---|
| **Port `Clock`** | Única fonte de "agora", UTC e não decrescente | `grolla.application.clock.Clock` |
| **Relógio do sistema** | Adapter real do `Clock` | `grolla.adapters.system_clock.SystemClock` |
| **Relógio fake** | Relógio parado que só anda quando o teste manda | `grolla.testing.clock.FakeClock` |
| **Incidentes (domínio)** | Severidade por regras, agrupamento por serviço na janela (só incidentes abertos: encerrado ou descartado não recebe alerta novo), estados Detectado/Informado seguindo a severidade (Em tratamento e Mitigado ficam como o humano deixou), eventos e cards — decisões puras | `grolla.domain.incidents.decide_firing` · `decide_resolved` · `OPEN_STATES` |
| **War room (domínio)** | Ações humanas e a máquina de estados: assumir como IC (Detectado/Informado → Em tratamento, grava o TTA), mudar a severidade para cima ou para baixo, registrar ação, descartar hipótese, marcar mitigado (só Em tratamento, grava o TTM), reabrir (só Mitigado), encerrar e descartar (motivo obrigatório, grava o TTR). Resolvido e Descartado são finais. Cada ação vira evento com o autor; ação inválida ou sem autor levanta `InvalidActionError` com a explicação. Cards: "Ana assumiu como IC", "P1 → P3 por Ana", "Mitigado por Ana", "Encerrado por Ana · durou 35 min" e "Descartado por Ana", com "Motivo: …" na evidência | `grolla.domain.war_room.decide_action` |
| **Ações no war room** | Trava o incidente (`incident_for_update`), decide e grava incidente, eventos e card numa transação. O card de encerramento leva a validação (causa raiz) e, na evidência, as ações registradas pelo time (`- <texto> (<autor>)`) | `grolla.application.war_room.act` |
| **Acompanhamento da crise** | `cycle_due` decide por varredura se o incidente acompanhado precisa de ciclo (análise terminada, nenhum em andamento, abaixo de `max_cycles`, intervalo vencido ou pedido antecipado depois de 1 min do último); `schedule_cycles` não enfileira acima de `max_tokens_per_incident` e registra `follow_up_stopped` uma vez. `run_next_cycle` usa o contexto do chat (`chat_context`, com a memória), o último ciclo concluído e as ferramentas, com tempo e orçamento por ciclo; falha vira ciclo parcial. `decide_cycle` posta o card "Atualização" quando a tendência difere do último card ou o modelo diz que mudou, o curto "sem mudança: estável, 12% de erro" após `quiet` sem card, e propõe mitigar (detectado/informado/em tratamento) ou encerrar (mitigado) após `stable` de `normalizado` seguidos, uma vez por estado; o último ciclo permitido registra `follow_up_stopped`. Impacto estimado: `per_minute` × taxa de erro × minutos desde o ciclo anterior ("R$ 3.000,00 estimado"). Alerta que volta a disparar num mitigado o devolve a em tratamento (`state_changed` com `reason`, card `alert_reopened`), mantendo IC, TTA e TTM. O alerta agrupado e a mensagem no chat pedem o ciclo antecipado (`request_cycle`) | `grolla.domain.follow_up` · `grolla.application.follow_up` · `FOLLOW_UP_PROMPT` · `follow_up_text` |
| **Post mortem** | Ao encerrar como resolvido (não no descarte), `act` pede o rascunho (`request_post_mortem`) na mesma transação. `run_next_post_mortem` monta o contexto como no chat, com os ciclos, as métricas e a timeline completa, e chama `draft_post_mortem` sem ferramentas, com teto de tokens e tempo; falha grava o post mortem mesmo assim, com métricas e timeline e as demais seções "não gerada: <motivo>". `metrics_text` (TTD do primeiro alerta à detecção; TTA, TTM e TTR da detecção, "—" quando não houve) e `timeline_text` (`- 10:04 [pessoa] Ana: assumiu como IC`, origem alerta/agente/chat/pessoa, sem os eventos de entrega de cards) não usam o modelo. Edição por seção com versão (autor, data, motivo, texto anterior); a primeira mudança humana passa rascunho a em revisão. Regenerar reescreve uma seção com o documento atual como dado (a determinística é recalculada do log); aprovado e encerrado não mudam. Ciclo de vida rascunho → em revisão → aprovado → encerrado (aprovado volta a em revisão); aprovar exige dono e prazo e encerrar exige concluído ou aceito, com a mensagem nomeando os itens. PDF só de aprovado ou encerrado. O pedido preso além do timeout + 2 min termina "interrompida" | `grolla.domain.post_mortem` · `grolla.application.post_mortem` · `POST_MORTEM_PROMPT` · `post_mortem_text` · `regeneration_text` |
| **Fakes do post mortem** | `InMemoryPostMortemStore` (o pedido do rascunho vira um pedido pendente, um por incidente; fila de regenerações; lista com filtros e vencidos; uso em `repository.usages`), `FakeDocumentRenderer` (bytes `%PDF-` com o título), `ScriptedLanguageModel` com `post_mortem`, `post_mortem_error`, `post_mortem_delay`, `regeneration` e `regeneration_error`, e os construtores `make_post_mortem` e `make_action` | `grolla.testing.post_mortem` · `grolla.testing.investigation` |
| **Menos ruído: triagem** | Com `IntakePolicy.triage` (seção `noise`), o alerta que abre um incidente pede a triagem (`request_triage`) na mesma transação. `run_next_triage` lê os outros incidentes abertos com alerta na janela (`open_since`); sem outro de outro serviço e com a severidade vinda de uma regra (`severity_rule` do `alert_received`), termina sem o modelo (`needs_model`). Senão, chama `triage` sem ferramentas, com os alertas, o resumo de cada outro (regras dos alertas e resumo da análise) e a memória dos serviços de todos, com teto de tokens e tempo. `decide_triage` grava `triage_completed` com o decidido; `merge_suggested` (evento no incidente novo e card "Parece o mesmo problema que <título do outro>", com o motivo) só quando `same_as` é um dos outros; com a severidade padrão, sugestão mais alta sobe a severidade (`severity_changed` com "sugerida pelo agente: <motivo>", card de severidade elevada, Detectado/Informado seguem a severidade), nunca abaixa. Falha do modelo termina a triagem com o motivo, sem mudar o incidente; presa além do timeout + 2 min, "interrompida". `merge_incidents` trava os dois em ordem de id e aplica `decide_merge`: o juntado fica descartado com "juntado em <título do principal>" (`incident_discarded` e `merged_into`) e sai dos disparando; o principal recebe serviços, disparando, último alerta e a pior severidade (`incident_merged`, card de incidentes juntados); recusa o mesmo incidente, sem autor e incidente fechado | `grolla.domain.triage` · `grolla.application.triage` · `TRIAGE_PROMPT` · `triage_text` |
| **Fakes da triagem** | `InMemoryTriageQueue` (o pedido em `triage_requests` vira um pendente, um por incidente; `finish` só em andamento grava incidente, eventos, cards e uso) e `ScriptedLanguageModel` com `triage_draft` (padrão: nada a juntar, sem severidade) e `triage_error`; suítes `TriageQueueContract` e `TriageModelContract` | `grolla.testing.triage` · `grolla.testing.investigation` |
| **Chat com o agente** | A pergunta vira mensagem pendente e evento `human_message`. A resposta usa as mesmas ferramentas, com tempo, orçamento e teto por incidente próprios (`chat_timeout`, `chat_max_tokens`, `chat_max_tokens_per_incident`, etapa `warroom_chat`); teto atingido responde "limite do chat deste incidente atingido" sem chamar o modelo; toda falha vira "não consegui responder: <motivo>" com as consultas feitas; o reaper responde "interrompida". Contexto compacto: incidente, análise, validação, últimos 30 eventos e últimas 20 mensagens. O prompt é somente leitura e pede evidência | `grolla.application.war_room.ask` · `grolla.application.chat.run_next_reply` · `reap_stuck_replies` · `chat_context` · `CHAT_PROMPT` |
| **Receber alertas** | Filtro por time, deduplicação e uma transação por alerta; incidente novo grava o pedido de análise (`analysis_requested`) na mesma transação | `grolla.application.incidents.receive_alerts` |
| **Análise (domínio)** | Análise concluída (hipóteses por confiança) ou parcial (motivo e o que foi avaliado); texto de cada motivo para card e página | `grolla.domain.analysis.complete` · `partial` · `PARTIAL_REASON_TEXT` |
| **Validação (domínio)** | Veredito por hipótese (confirmada, rejeitada ou inconclusiva), causa raiz e plano de ações concretas (`action`, `target`, `expected`). `check_plan` recusa ação que começa com verbo de investigação (investigar, verificar, checar…, sem caixa nem acento), ações sem causa raiz e causa raiz sem ação. Sem confirmação, a validação é concluída sem plano e com o que faltou; parcial só em falha. Links só se vieram de uma avaliação | `grolla.domain.analysis.verified` · `verification_partial` · `check_plan` |
| **Ferramentas da investigação** | `query_metric`, `search_logs`, `find_traces`, `recent_deploys` e as de nuvem `cloud_resources`, `cloud_changes` e `cloud_alarms` (só `service`; sem a seção `cloud`, respondem que a nuvem não está configurada) com JSON Schema fechado e janela do `lookback`; falha vira avaliação "não foi possível consultar", nunca exceção | `grolla.application.investigation.Toolbox` |
| **Orçamento e prompt** | Limite de tokens por análise (entrada, saída e cache); roteiro curto neutro (impacto, deploys, traces, logs, infraestrutura na nuvem; rollback só com o deploy antes do problema) e mensagem montada do incidente; roteiro da validação (veredito, causa raiz, plano concreto) e mensagem com as hipóteses e avaliações da análise inicial | `TokenBudget` · `SYSTEM_PROMPT` · `brief_text` · `VERIFICATION_PROMPT` · `verification_text` |
| **Rodar a análise** | Reivindica o pedido, investiga sob timeout, converte toda falha em parcial e grava análise, evento, card `analysis_ready` e uso do modelo numa transação; o reaper encerra como "interrompida" o que passou do timeout + 2 min | `grolla.application.analysis.run_next_analysis` · `reap_stuck_analyses` |
| **Rodar a validação** | A análise inicial com hipóteses grava o evento `verification_requested` e a fila cria o pedido. A validação usa o tempo e o orçamento próprios, converte toda falha em parcial (plano não concreto vira resultado inválido) e grava a validação, `verification_completed`/`verification_partial` e o card `root_cause` (causa raiz ou "causa raiz não confirmada", e a primeira ação ou o que faltou); o reaper encerra como interrompida | `grolla.application.analysis.run_next_verification` · `reap_stuck_verifications` |
| **Ports da investigação** | `Telemetry`, `ChangeHistory`, `CloudInventory` (com `NoCloudInventory`), `LanguageModel` (`investigate`, `verify` e `reply`), `AnalysisQueue` (análise e validação), `ChatQueue` (respostas do chat) | `grolla.application.investigation` · `grolla.application.analysis` |
| **Fakes da investigação** | Telemetria, histórico e nuvem roteirizados, modelo que chama ferramentas e devolve, falha ou demora (na análise, na validação e no chat), filas de análises, de validações e do chat em memória | `grolla.testing.investigation` · `FakeCloudInventory` · `InMemoryAnalysisQueue` · `InMemoryChatQueue` · `make_analysis` · `make_verification` · `make_verification_draft` |
| **Entregar cards** | Outbox em ordem por incidente, espera dobrando a cada falha, desistência no limite | `grolla.application.incidents.deliver_notifications` |
| **Ports de incidentes** | `AlertIngress`, `ChatNotifier`, `IncidentRepository`/`IncidentUnitOfWork`, `NotificationOutbox` | `grolla.application.incidents` |
| **Fakes de incidentes** | Repositório e outbox em memória, canal de chat que grava e falha sob comando, entrada de alertas | `grolla.testing.incidents` · `grolla.testing.builders` |
| **Postgres** | Engine async, ping, heartbeats | `grolla.adapters.postgres.PostgresDatabase` |
| **Repositório de incidentes** | Unidade de trabalho transacional, deduplicação de alertas, log de eventos e projeção | `grolla.adapters.postgres.PostgresIncidentRepository` |
| **Outbox de notificações** | Fila de cards com retentativa; entrega e desistência viram evento no incidente | `grolla.adapters.postgres.PostgresNotificationOutbox` |
| **Filas de análise e de validação** | `AnalysisQueue`: reivindicação com `SKIP LOCKED`, pedidos presos e encerramento numa transação (resultado, eventos, card e `llm_usage`), para a análise inicial e para a validação; o `finish` da análise com hipóteses já pede a validação | `grolla.adapters.postgres.PostgresAnalysisQueue` |
| **Fila dos ciclos de acompanhamento** | `CycleQueue`: candidatos (incidentes abertos com análise, ciclos, pedido antecipado e tokens), enfileiramento idempotente, reivindicação com `SKIP LOCKED`, ciclos presos e encerramento numa transação (ciclo, eventos, cards e `llm_usage`) | `grolla.adapters.postgres.PostgresCycleQueue` |
| **Fila do chat do war room** | `ChatQueue`: a pergunta é a fila (`SKIP LOCKED`), perguntas presas, resposta numa transação (mensagem do agente, desfecho na pergunta, eventos e `llm_usage`) e tokens já gastos no chat do incidente | `grolla.adapters.postgres.PostgresChatQueue` |
| **Post mortem** | `PostMortemStore`: documento com seções, plano de ação e avaliação do agente em `jsonb`, versões só inserção, lista com filtros e itens vencidos, fila de rascunho e regeneração com `SKIP LOCKED` | `grolla.adapters.postgres.PostgresPostMortemStore` |
| **Fila da triagem** | `TriageQueue`: reivindicação com `SKIP LOCKED`, triagens presas, incidentes abertos na janela e encerramento numa transação (incidente, eventos, cards e `llm_usage`) | `grolla.adapters.postgres.PostgresTriageQueue` |
| **Métricas do agente** | `MetricsReader.facts(since)`: uma consulta por parte (incidentes resolvidos, post mortems aprovados ou encerrados com a avaliação e a última resposta do "já vi isso" de cada incidente, lida do log de eventos) | `grolla.adapters.postgres.PostgresMetricsReader` |
| **Memória viva** | `MemoryStore`: dossiês, episódios e notas com busca textual em português (`tsvector` + `websearch_to_tsquery`), contexto da investigação (ambiente, serviços, integrações citadas, notas e episódios parecidos), gravação com versões numa transação e listagens da página; trechos com embedding (pgvector) e busca híbrida em SQL (vetor + texto com RRF) no `context_for` e no `search_chunks`; `LearningQueue` e `StudyQueue` com `SKIP LOCKED` | `grolla.adapters.postgres.PostgresMemoryStore` · `PostgresLearningQueue` · `PostgresStudyQueue` |
| **Memória com RAG (núcleo)** | `chunk_text` recorta por seção `#`/`##` (seção longa por parágrafo, nunca trecho vazio). `index_memory` indexa em lote os registros pendentes (`unindexed`), com o título no trecho do episódio e da nota, e limpa os trechos velhos; `index_documents` indexa os `.md` do serviço no estudo. `query_for` monta a `Query` com vetor (falha do embedder vira consulta só textual). Com `embedder`, `memory_for` usa a busca híbrida (determinístico primeiro, depois RRF de vetor e texto) e traz até 3 trechos de documentação, que `memory_text` mostra no bloco `<documentacao>` (`### [documento] <serviço>: <caminho> — <seção>`, texto e `Link:`); `recall` devolve trechos (`chunks_text`, bloco `<memoria>` com `## Trechos`) | `grolla.application.memory_retrieval` · `grolla.application.memory_store.memory_for` · `grolla.domain.memory.chunk_text` |
| **Fakes da memória com RAG** | `FakeTextEmbedder` determinístico e sem rede (saco de palavras normalizado por hash, com `concepts` para roteirizar paráfrases), `InMemoryMemoryStore` com trechos e busca híbrida em Python (RRF k=60) e `FakeChangeHistory.documents` | `grolla.testing.memory` · `grolla.testing.investigation` |
| **Migrações** | Alembic sob advisory lock; idempotente e seguro em paralelo | `grolla.adapters.postgres.migrate` |
| **Entrada de alertas (SigNoz)** | `AlertIngress`: HTTP Basic em tempo constante; payload do webhook (formato Alertmanager) vira `Alert` neutro com o bruto em `raw`; serviço do primeiro label de `service_labels` ou `desconhecido` | `grolla.adapters.signoz.SignozAlertIngress` |
| **Cards no chat (Teams)** | `ChatNotifier`: Adaptive Card por tipo de notificação postado no Workflow do canal, timeout de 10 s; HTTP ≥ 400 ou erro de rede viram `NotificationFailedError` (sem expor a URL). O incidente novo é o card "Estou investigando", que lista o que vai ser checado. O card de análise traz o resumo e as seções "O que avaliei", "Hipóteses" (com confiança e evidência) e "Plano proposto", além dos botões "Abrir incidente" e "Ver no SigNoz". A análise parcial ("Análise parcial") diz o motivo logo abaixo do título; a com hipóteses avisa "Estou validando as hipóteses; a causa raiz vem em seguida.". O card da validação abre com "Causa raiz · P1", "Causa raiz não confirmada · P1" ou "Validação parcial · P1" (com "Motivo: …" logo abaixo) e traz as seções "Causa raiz", "Hipóteses" (`- <hipótese>: confirmada/rejeitada/inconclusiva — <justificativa>`), "Plano de ação" (`- <ação> (<alvo>) → <efeito>`, com link quando houver) e "O que faltou", só com o botão "Abrir incidente". O card "Incidente normalizado" fecha a história: duração ("Normalizado: durou 32 min") e o desfecho da validação (causa raiz e "Plano de ação sugerido", causa não confirmada com o que faltou, ou validação em andamento). As ações humanas do war room ganham cards curtos com o autor: "IC assumiu · P1", "Severidade alterada · P3" (com o motivo, se houver), "Incidente mitigado · P1", "Incidente descartado · P1" (com "Motivo: …") e "Incidente encerrado · P1", com "Encerrado por Ana · durou 35 min", o motivo, as ações registradas pelo time e a seção "Causa raiz" quando a validação a confirmou. As mensagens do chat não vão para o canal Com "já vi isso" na análise, o card de análise ganha, logo depois do resumo, a seção "Já vi isso: <título do episódio> (dd/mm)" com o motivo e o link "Abrir o incidente anterior" (a página deste incidente com o id trocado). O acompanhamento da crise tem três cards, todos com o botão "Abrir o war room": "Atualização · P1", com a tendência (Piorando, Estável, Melhorando, Normalizado, na cor dela), a taxa de erro ("12,5%" ou "não medida"), o impacto estimado quando houver, o resumo do ciclo e a seção "Evidências" com links; o card curto "Sem mudança · P1" (notificação sem `cycle`), com o texto pronto do núcleo e o impacto; "Grolla sugere marcar como mitigado · P1" (o título é o `summary`), com o motivo e o aviso de que o estado só muda pelo botão no war room; e "Alerta voltou a disparar · P1", com o texto do núcleo e o link do alerta na origem. Menos ruído tem dois cards, também com o botão "Abrir o war room": "Parece o mesmo problema que <outro> · P1" (o título é o `summary`), com o motivo e o aviso de que juntar é pelo botão Juntar no war room; e "Incidentes juntados · P1", com o texto e o motivo do núcleo | `grolla.adapters.teams.TeamsChatNotifier` |
| **Modelo de linguagem (Anthropic)** | `LanguageModel`: laço de ferramentas manual sobre o `Toolbox` (tool_use em paralelo), resultado final por structured outputs (`output_config.format` com o schema do rascunho), thinking adaptativo, `effort` e modelo do YAML e `cache_control` de 1 h no system e nas tools. Faz `budget.spend` a cada resposta, com o modelo que respondeu. Usa 2 retentativas e timeout de 60 s por requisição. Erro da API ou recusa viram `LanguageModelError`; JSON fora do schema ou resposta truncada viram `InvalidAnalysisError`. A validação (`verify`) usa o mesmo laço com `VERIFICATION_PROMPT`, o schema do `VerificationDraft` e modelo e `effort` de `llm.stages.verification` (sem ela, os da análise), com o uso na etapa `verification`; plano que `check_plan` recusa ganha uma única mensagem de correção e, se voltar, vira `InvalidAnalysisError`. A resposta do chat do war room (`reply`) usa o mesmo laço com `CHAT_PROMPT`, uma mensagem com o contexto do incidente e, por último, `Pergunta: <pergunta>`, o schema `{"reply": string}` e modelo e `effort` de `llm.stages.warroom_chat` (sem ela, os da análise), com o uso na etapa `warroom_chat`; texto vazio ou fora do schema vira `InvalidAnalysisError`. O schema da análise tem `seen_before` (objeto `{incident_id, reason}` ou null, obrigatório); id que não é UUID ou motivo vazio viram `None`, sem invalidar a análise. A memória usa mais três chamadas: `study_service` roda o mesmo laço com `STUDY_PROMPT` e o schema `{dossier}`, fechando no orçamento como o chat, e devolve o dossiê do serviço com as fontes tiradas dos links das avaliações (nunca do modelo); `learn` (`LEARNING_PROMPT`, schema do episódio, `noise` e dossiês de serviço/integração) e `synthesize` (`SYNTHESIS_PROMPT`, schema `{environment, integrations}`) são uma chamada só, sem ferramentas. Modelo e `effort` de `llm.stages.learning` e `llm.stages.study` (sem elas, os da análise), com o uso nas etapas `learning` e `study`. O ciclo de acompanhamento (`follow_up`) roda o mesmo laço com `FOLLOW_UP_PROMPT` e a mensagem de `follow_up_text` (contexto do incidente e ciclo anterior), fechando no orçamento como o chat, com o schema `{trend (piorando/estavel/melhorando/normalizado), error_rate (número ou null), summary, changed, evidence [{text, url}]}`; taxa fora de 0 a 100, resumo vazio ou JSON fora do schema viram `InvalidAnalysisError`, e só as três primeiras evidências ficam. Modelo e `effort` de `llm.stages.follow_up` (sem ela, os da análise), com o uso na etapa `follow_up`. O post mortem usa duas chamadas sem ferramentas com `POST_MORTEM_PROMPT`, modelo e `effort` de `llm.stages.post_mortem` (sem ela, os da análise) e o uso na etapa `post_mortem`: `draft_post_mortem` (mensagem de `post_mortem_text`) com o schema de um campo de texto por seção escrita pelo modelo (`resumo`, `impacto`, `deteccao`, `fatores`, `hipoteses`, `licoes`, `plano`, `avaliacao_agente`) e `actions [{description, kind (prevenir/detectar/mitigar)}]`, sem as seções determinísticas; seção vazia ou JSON fora do schema viram `InvalidAnalysisError`, e item sem descrição é descartado. `regenerate_section` (mensagem de `regeneration_text`) devolve o schema `{body}`; texto vazio vira `InvalidAnalysisError`. A triagem (`triage`) é uma chamada sem ferramentas com `TRIAGE_PROMPT`, a mensagem de `triage_text`, modelo e `effort` de `llm.stages.triage` (sem ela, os da análise) e o uso na etapa `triage`, com o schema `{same_as (string ou null), same_reason, severity (P0..P3 ou null), severity_reason}`; `same_as` que não é UUID vira `None` (sem junção), sem invalidar, e JSON fora do schema vira `InvalidAnalysisError` | `grolla.adapters.anthropic.ClaudeLanguageModel` |
| **PDF do post mortem (fpdf2)** | `DocumentRenderer`: A4, Helvetica, cabeçalho com o título do incidente, severidade, serviços, status (com autor e data) e as datas de detecção e resolução; as seções na ordem de `SECTION_TITLES`, com o markdown simples convertido (`#`/`##` viram subtítulo em negrito, `- `/`* ` ganham recuo, `**` sai); o plano de ação como tabela (descrição, tipo, dono, prazo, situação) logo depois da seção "Plano de ação"; logo depois da seção "Avaliação do agente", os campos da avaliação do time ("Hipótese principal certa: sim/não/sem resposta", "Minutos até a hipótese certa: N" ou "-", "Memória ajudou: sim/não/sem resposta" e "Avaliado por <autor> em <data>" quando houver). O texto vai em latin-1: aspas curvas, travessões e reticências viram ASCII e o resto (emoji) vira `?`, sem nunca falhar por caractere; falha do fpdf vira `RenderError` | `grolla.adapters.fpdf.FpdfDocumentRenderer` |
| **Telemetria (MCP do SigNoz)** | `Telemetry` pelo `signoz-mcp-server` (streamable HTTP, sem chave: ela fica no container do servidor). Lista fechada de leitura (`signoz_aggregate_traces`, `signoz_search_logs`, `signoz_search_traces`, `signoz_get_service_top_operations`); outra ferramenta é recusada sem chamar o servidor. Sinais neutros viram agregações sobre os spans (`taxa_de_erro` = spans com erro ÷ total, `latencia_p99` = p99 de `duration_nano`, `vazao` = `rate()`), em pontos de 1 min, com o resumo dizendo início, fim, máximo e "Mudou às HH:MM, de X para Y" (o primeiro ponto a mais de 3x da média dos 5 primeiros, ou a mais de 5 pontos percentuais na taxa de erro), e só os últimos 10 pontos. Logs são WARN e acima (`severity_number >= 13`), agrupados com números longos, IPs, `0x…` e UUIDs normalizados, nas 5 mensagens mais frequentes (`31x [ERROR] …`, só a primeira linha). Resumo compacto em português com link para o SigNoz; timeout de 20 s por chamada; erro, timeout ou resposta fora do formato viram `ToolUnavailableError`. Para a memória, `alert_rules` lista as regras ligadas (`signoz_list_alert_rules`, paginado) e busca a condição de cada uma em `signoz_get_alert`, em texto ("count() de spans com <filtro> acima de 20 (pelo menos uma vez) na janela de 5m. Resumo: …"), com o serviço pelos `service_labels` do YAML (ou `desconhecido`) e o link da regra; `active_services` vem de `signoz_list_services`; `dependencies` agrega os spans de saída (`kind_string IN ('Client', 'Producer')`) por `peer.service`, `db.system`, `db.name` e `server.address` em três consultas escalares (chamadas, com erro, p99) e responde "banco postgresql pedidos (grolla-lab-db): 79367 chamadas, 0,0% com erro, p99 5.002 ms", ou "nenhuma dependência vista nos traces na janela" | `grolla.adapters.signoz.telemetry.SignozTelemetry` |
| **Deploys (Azure DevOps)** | `ChangeHistory` pela API REST (`_apis/build/builds?repositoryId=…&repositoryType=TfsGit&minTime=…`), PAT somente leitura em Basic. Repositório pelo mapa (exceções) ou pelo nome do serviço nos `projects` (`_apis/git/repositories/{nome}`), com cache em memória; sem repositório, responde "serviço sem repositório mapeado". Até 10 builds com horário, versão, branch, primeira linha da mensagem do commit (`triggerInfo["ci.message"]` ou `sourceVersionMessage`; sem elas, a API de commits do repositório, só para os builds listados, e falha nessa busca só omite a mensagem), autor e resultado, e link do pipeline; HTTP ≠ 200, rede ou formato inesperado viram `ToolUnavailableError`. `read_file` usa a Git Items API (`…/items?path=…&includeContent=true`) no branch padrão do repositório: arquivo com o conteúdo (cortado em 20.000 caracteres, com "arquivo cortado" no resumo), binário recusado com "arquivo binário não lido", caminho vazio ou de pasta com a listagem de um nível (`recursionLevel=OneLevel`, pastas terminam em `/`), arquivo inexistente como resposta, e o link do arquivo na web (`_git/<repo>?path=…`). `documents` (a documentação para a memória com RAG) lista a árvore inteira (`scopePath=/&recursionLevel=Full`), busca o conteúdo dos `.md` (sem diferenciar maiúsculas, até 100, porque a listagem não traz o tamanho), pula binários e devolve os menores primeiro até `max_files` e `max_bytes`, com o caminho sem `/` inicial e o link; sem repositório, `[]` | `grolla.adapters.azure_devops.AzureDevOpsChangeHistory` |
| **Embeddings locais (fastembed)** | `TextEmbedder` com ONNX Runtime no próprio processo, sem rede: `small` = `paraphrase-multilingual-MiniLM-L12-v2` (384 dim, sem prefixo), `large` = `multilingual-e5-large` (1024 dim, com `query: `/`passage: `). Carrega o modelo no construtor só dos arquivos locais (`/opt/grolla/models`, embutido na imagem do worker pelo build arg `GROLLA_RAG_MODELS`); modelo ausente levanta `EmbeddingError` nomeando o modelo e o build arg. `embed` roda em `asyncio.to_thread`, devolve vetores normalizados na ordem dos textos e falha do ONNX vira `EmbeddingError` | `grolla.adapters.fastembed.FastembedTextEmbedder` |
| **Nuvem (AWS)** | `CloudInventory` com boto3 em `asyncio.to_thread` e lista fechada de leitura (`tag:GetResources`, `rds:DescribeDBInstances`, `ec2:DescribeInstances`, `elbv2:DescribeLoadBalancers`, `cloudtrail:LookupEvents`, `cloudwatch:DescribeAlarms`). Recursos pela tag `service=<nome>` em cada região mais o `resource_map` (ARNs; identificador solto fica na primeira região), com o estado de banco RDS, máquina EC2 e balanceador ELBv2 e o link do console. Mudanças pelo CloudTrail por `ResourceName`, sem leituras, `HH:MM EventName em <recurso> por <usuário>` (até 20, com o aviso do atraso de 5 min). Alarmes em `ALARM` cujas dimensões citam os recursos. Credencial, permissão ou rede viram `ToolUnavailableError`. `overview` junta, em cada região, tudo que tem tag (Tagging API paginada) e todos os bancos, máquinas e balanceadores dos describes (inclusive os sem tag), agrupados pela tag `service` e pelo tipo, com o grupo "sem serviço" por último, o estado e até 200 recursos | `grolla.adapters.aws.AwsCloudInventory` |
| **Configuração** | Variáveis `GROLLA_*` validadas por processo (`ApiSettings`, `WorkerSettings`); erro nomeia a variável | `grolla.bootstrap.load_settings` |
| **Configuração do time** | YAML com filtro de labels, labels de serviço, janela e regras de severidade; erro aponta o campo | `grolla.bootstrap.load_team_config` |
| **Composição** | Migra e monta as dependências de cada processo | `grolla.bootstrap.prepare_api` · `prepare_worker` |

## 🏛 Arquitetura

| Módulo | Papel | Pode importar |
|---|---|---|
| `grolla.domain` | Regras puras — sem framework, sem infraestrutura, sem nome de fornecedor | — |
| `grolla.application` | Casos de uso + ports (`Protocol`) | `domain` |
| `grolla.adapters.<fornecedor>` | Implementações dos ports; independentes entre si | `application`, `domain` |
| `grolla.bootstrap` | Composition root — único que liga port → adapter | todos |
| `grolla.testing` | Fakes em memória dos ports, para testes de qualquer pacote | `application` |

Fornecedor novo = subpacote novo em `grolla.adapters`, passando na suíte de contrato do port, e
uma linha no `bootstrap`. Nada em `domain` ou `application` muda.

## 🗄 Banco de dados

- Migrações em `src/grolla/adapters/postgres/migrations/versions/`, numeradas (`0001_…`), **só avançam**.
- `0001`: schema da fila do procrastinate + tabela `heartbeats`.
- `0002`: incidentes, log de eventos, alertas recebidos e outbox (abaixo).
- `0003`: fila e resultado das análises iniciais e uso do modelo de linguagem (abaixo).
- `0004`: fila e resultado da validação das hipóteses até a causa raiz (abaixo).
- `0005`: war room: IC e horários do incidente e o chat com o agente (abaixo).
- `0006`: memória viva: registros, versões, fila do aprendizado e rodadas de estudo (abaixo).
- `0007`: memória com RAG: extensão `vector` (pgvector) e os trechos indexados (abaixo).
- `0008`: acompanhamento da crise: ciclos por incidente e o pedido de ciclo antecipado (abaixo).
- `0009`: post mortem: o documento por incidente, as versões das seções e a fila de rascunhos e
  regenerações (abaixo).
- `0010`: métricas do agente: a avaliação estruturada no post mortem (`post_mortems.evaluation`).
- `0011`: menos ruído: a fila de triagem dos incidentes novos (`triages`, abaixo).
- `migrate(url)` segura `pg_advisory_xact_lock` durante o upgrade: api e worker podem subir juntos.

### Tabelas do S2

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `incidents` | Projeção do estado atual de cada incidente (`services` e `firing` em `text[]`) | atualizada na mesma transação dos eventos |
| `incident_events` | Log de eventos do incidente (`data` em `jsonb`) | **só inserção**: trigger recusa `UPDATE`, `DELETE` e `TRUNCATE` |
| `alerts` | Cada alerta recebido, com labels e payload bruto (`jsonb`) e o incidente a que foi ligado | `UNIQUE (fingerprint, starts_at, status)` deduplica reenvios |
| `outbox` | Notificações a entregar (`notification` em `jsonb`, com a análise completa no card de análise), tentativas e próximo horário | — |

### Tabelas do S3

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `analyses` | A análise inicial de cada incidente: é a fila (`pendente` → `em_andamento`) e a projeção do resultado (`concluida`/`parcial`), com `evaluations`, `hypotheses` e `plan` em `jsonb` e o `partial_reason` | `incident_id` é a chave: **uma análise por incidente**; índices parciais nos pendentes e nos em andamento |
| `llm_usage` | Uma linha por resposta do modelo: etapa, modelo, tokens de entrada, saída e cache, horário | — |

### Tabela da validação (0004)

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `verifications` | A validação das hipóteses de cada incidente, com a mesma forma da `analyses`: é a fila (`pendente` → `em_andamento`) e a projeção do resultado (`concluida`/`parcial`), com `verdicts`, `actions` e `evaluations` em `jsonb`, a `root_cause`, o que faltou (`missing`) e o `partial_reason` | `incident_id` é a chave: **uma validação por incidente**; índices parciais nos pendentes e nos em andamento |

### War room (0005)

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `incidents` (colunas novas) | `commander` e os horários `handling_at` (TTA), `mitigated_at` (TTM) e `resolved_at` (TTR, encerrado ou descartado) | nulos nos incidentes antigos |
| `chat_messages` | Mensagens do chat, em ordem de `at` e `id`, com `evaluations` em `jsonb` nas do agente. A pergunta de uma pessoa leva `reply_status` (`pendente` → `em_andamento` → `respondida`/`falhou`) e é a fila; a resposta do agente aponta a pergunta em `reply_to` | índice parcial nas pendentes |

### Memória viva (0006)

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `memories` | Dossiês, episódios e notas por assunto, com confiança, fonte e `active` (esquecer não apaga); coluna gerada `search` (`to_tsvector('portuguese', subject, title, body)`) | índice GIN na busca; **um dossiê por assunto** e **um episódio por (incidente, assunto)** (índices únicos parciais) |
| `memory_versions` | O texto e o `active` de um registro antes de cada mudança | só inserção pelo adapter |
| `learnings` | Fila do aprendizado ao encerrar (`pendente` → `em_andamento` → `concluido`/`falhou`), com tentativas e motivo | `incident_id` é a chave: um aprendizado por incidente |
| `study_runs` | Rodadas de estudo (`inicial`, `diario`, `completo`, `reaprender`) e o estado | **uma rodada pendente só** (índice único parcial) |
| `service_studies` | O último estudo de cada serviço: quando, se deu certo e o motivo | `service` é a chave |
| `llm_usage` (coluna nova) | `study_run_id`: o uso do estudo aponta a rodada, não um incidente | índice para somar os tokens da rodada |

### Memória com RAG (0007)

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `memory_chunks` | Um trecho por seção de um registro (`origin = memoria`, com `memory_id` e `kind`) ou de um `.md` do repositório (`origin = documento`, com `path` e `url`, assunto = serviço); o `model` e o `embedding` (`vector` sem dimensão fixa: só se compara com o mesmo modelo), `indexed_at` e a coluna gerada `search` (`to_tsvector('portuguese', subject, heading, text)`) | índice GIN na busca e índices em `memory_id` e `(origin, subject)`; **sem índice ANN**: a distância é exata |

### Menos ruído (0011)

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `triages` | A triagem de cada incidente novo: é a fila (`pendente` → `em_andamento`) e o desfecho (`feito`/`falhou`, com o motivo) | `incident_id` é a chave: **uma triagem por incidente**; índice parcial nos pendentes |

### Repositórios

- `PostgresIncidentRepository.transaction()` abre **uma transação** e entrega a unidade de
  trabalho (`register_alert`, `active_incidents`, `incident_firing`, `save`, `append`,
  `link_alert`, `enqueue`, `incident_for_update`, `add_message`, `request_analysis`, `request_learning`): commit ao sair sem erro, rollback em exceção. Assim o evento, a
  projeção e o card na outbox são gravados juntos ou nada é gravado.
- `register_alert` é um `INSERT … ON CONFLICT DO NOTHING RETURNING id`: devolve `False` para um
  reenvio, mesmo com dois processos.
- `active_incidents` segura um advisory lock por serviço e `FOR UPDATE` nos candidatos, e
  `incident_firing` trava o incidente: webhooks simultâneos do mesmo serviço não abrem dois
  incidentes, e resoluções simultâneas não perdem a atualização de `firing`
  (`tests/integration/test_postgres_concurrency.py`). Só entram incidentes em `OPEN_STATES`:
  depois de encerrado ou descartado, um alerta novo abre outro incidente.
- `incident_for_update` lê o incidente com `FOR UPDATE`: duas ações simultâneas no war room
  ficam em fila, e a segunda decide sobre o estado já gravado pela primeira
  (`tests/integration/test_postgres_war_room.py`).
- `add_message` grava a mensagem e a devolve com o `id` do banco; com `wants_reply`, ela entra
  na fila como `pendente`. `chat_of` devolve o chat em ordem de `at` e `id`.
- `PostgresChatQueue.claim` marca `em_andamento` a pergunta pendente mais antiga, com
  `started_at` e `FOR UPDATE SKIP LOCKED`, e `stuck` lista as em andamento desde antes do limite.
  `answer` grava numa transação a resposta (com `reply_to`), o desfecho na pergunta
  (`reply.reply_status`: `falhou`, ou `respondida` quando vem `None`/`respondida`), os eventos e o
  `llm_usage`. Só grava se a pergunta ainda está `em_andamento`: sai uma resposta só por pergunta.
  `chat_tokens` soma todos os tokens (entrada, saída e cache) do incidente na etapa
  `warroom_chat`.
- `PostgresNotificationOutbox.due` devolve os pendentes vencidos em ordem de criação; um card
  esperando retentativa segura os seguintes do mesmo incidente, para a ordem por incidente;
  `delivered` e `give_up` gravam, na mesma transação, o evento `notification_delivered` ou
  `notification_failed` com `{"kind", "attempts"[, "error"]}`.
- `request_analysis` (na unidade de trabalho) grava o pedido pendente na mesma transação que
  abre o incidente; um segundo pedido para o mesmo incidente mantém o primeiro
  (`ON CONFLICT DO NOTHING`). `analysis_of`
  devolve a análise em qualquer estado, ou `None` se nunca foi pedida.
- `PostgresAnalysisQueue.claim` marca em andamento o pendente mais antigo com
  `FOR UPDATE SKIP LOCKED`: workers em paralelo nunca pegam o mesmo pedido
  (`tests/integration/test_postgres_analysis.py`). `stuck` lista os em andamento desde antes do
  limite. `finish` grava, numa transação, a análise, os eventos, o card na outbox e o
  `llm_usage`: ou tudo, ou nada. Só grava se a análise ainda está `em_andamento`; se já foi
  encerrada (o reaper fechou como interrompida), não faz nada, e sai um card só por análise.
  Quando a análise tem hipóteses, o mesmo `finish` insere o pedido da validação (`pendente`
  desde `at`, `ON CONFLICT DO NOTHING`); sem hipóteses, não há validação.
- `claim_verification`, `stuck_verifications` e `finish_verification` fazem o mesmo sobre
  `verifications`, com as mesmas garantias (`tests/integration/test_postgres_verification.py`).
  `verification_of` devolve a validação em qualquer estado, ou `None` se nunca foi pedida. O
  card `root_cause` leva a validação completa no JSON da outbox; cards anteriores à 0004, sem a
  chave, voltam sem validação.
- `PostgresCycleQueue` (`cycles`, chave `(incident_id, number)`, fila e projeção ao mesmo tempo):
  `candidates` traz os incidentes em `OPEN_STATES` das severidades pedidas, com o fim da análise
  inicial, os ciclos em ordem, o pedido antecipado (`follow_ups`) e os tokens da etapa
  `follow_up`; `enqueue` devolve `False` se o número já existe (`ON CONFLICT DO NOTHING`);
  `claim` pega o pendente mais antigo com `FOR UPDATE SKIP LOCKED`; `stuck` lista os em
  andamento desde antes do limite; `finish` grava numa transação, só se o ciclo ainda está
  `em_andamento`, o ciclo, os eventos, os cards na outbox, o `llm_usage` e apaga o pedido
  antecipado (`CycleQueueContract` em `tests/integration/test_postgres_follow_up.py`). `request_cycle` (na unidade de
  trabalho) grava o pedido antecipado, e um pedido novo substitui o anterior; `cycles_of`
  devolve os ciclos em ordem de número. O card leva o ciclo e o impacto no JSON da outbox;
  cards anteriores à 0008 voltam sem eles.
- `PostgresMemoryStore.context_for` traz o dossiê do ambiente, os dos serviços (na ordem
  pedida) e os das integrações cujo nome aparece no dossiê de um desses serviços; as notas
  ativas e não consolidadas desses assuntos; e até `EPISODE_LIMIT` episódios ativos: primeiro os
  do mesmo serviço com uma das regras de alerta no texto, depois os do mesmo serviço, depois por
  `ts_rank` da busca; sem texto, os mais recentes do serviço. `search` usa
  `websearch_to_tsquery('portuguese')` (radicais: "bancos" acha "banco"), confirmados e recentes
  primeiro. `save` faz upsert pelo id e insere as versões numa transação: um dossiê duplicado
  desfaz tudo (`tests/integration/test_postgres_memory.py`).
- Busca híbrida (`search_chunks`): trechos de documentos e de registros ativos do modelo da
  consulta; a CTE `vec` pega os K mais próximos por `embedding <=> vetor` (cosseno), a `txt` os
  K melhores por `ts_rank`, com K = max(4 × limite, 20), e a fusão é RRF
  (`score = Σ 1/(60 + posição)`). Sem vetor, só a textual. O vetor vai como literal de texto
  (`[0.1,0.2]`) com `CAST(... AS vector)`. Com vetor, `context_for` ordena os episódios depois do
  determinístico pelo RRF do melhor trecho de cada episódio e da busca textual do registro
  (episódio de outro serviço só entra no top K de um dos dois) e traz até `DOCUMENT_LIMIT`
  trechos de documentação dos serviços. `unindexed` lista os ativos sem trecho do modelo ou com
  `updated_at` depois do último `indexed_at`; `replace_chunks` e `replace_documents` apagam e
  inserem numa transação (o documento troca todos os trechos do serviço); `drop_stale_chunks`
  apaga os de outro modelo e os de registros esquecidos.
- `request_learning` (na unidade de trabalho) volta o pedido a `pendente` com as tentativas
  zeradas. `PostgresLearningQueue.claim` conta a tentativa; `finish` só grava se em andamento,
  volta a pendente enquanto `attempts < LEARNING_ATTEMPTS` e grava o `llm_usage` com o
  incidente; `failed` traz o título do incidente.
- `PostgresStudyQueue.request` não duplica uma rodada pendente ou em andamento (`reaprender`
  substitui a pendente), nem com pedidos simultâneos; `claim` usa `SKIP LOCKED`; `service_done`
  grava o estudo do serviço (uma falha mantém a data do último estudo bem feito) e o uso com a
  rodada; `tokens` é a soma desse uso; `service_order` põe primeiro os nunca estudados, os que
  falharam ou ficaram sem orçamento, depois os estudados há mais tempo.
- `PostgresPostMortemStore` (`post_mortems`, `post_mortem_versions`, `post_mortem_jobs`): o
  documento é uma linha com as seções, o plano de ação e a avaliação do agente em `jsonb` (datas
  ISO, ids como texto; avaliação nula no banco é a `AgentEvaluation()` vazia);
  `save` faz upsert e insere as versões (só inserção) numa transação; `versions` traz as de uma
  seção, mais recentes primeiro; `summaries` filtra por status, severidade, serviço
  (`ANY(services)`) e período (dia da detecção em UTC, inclusive), mais recentes primeiro, e
  conta em SQL os itens abertos com prazo antes de `today`. A fila vai de `pendente` a
  `em_andamento` (`claim` com `FOR UPDATE SKIP LOCKED`) e a `feito` ou `falho` (com o motivo);
  `finish` só grava se o pedido está em andamento (documento, versões e `llm_usage`).
  `request_post_mortem` (na unidade de trabalho) pede o rascunho uma vez só por incidente (índice
  único parcial `post_mortem_jobs_draft`); `request_regeneration` sempre insere um pedido
  (`tests/integration/test_postgres_post_mortem.py`).
- `request_triage` (na unidade de trabalho) pede a triagem uma vez só por incidente
  (`ON CONFLICT DO NOTHING`). `PostgresTriageQueue.claim` pega o pendente mais antigo com
  `FOR UPDATE SKIP LOCKED`; `stuck` lista os em andamento desde antes do limite; `open_since`
  traz os incidentes em `OPEN_STATES` com o último alerta desde `since`, mais recentes primeiro;
  `finish` grava numa transação, só se a triagem ainda está `em_andamento`, o incidente (se
  mudou, pelo mesmo `save` da unidade de trabalho), os eventos, os cards na outbox e o
  `llm_usage` do incidente, e encerra como `feito` ou, com motivo, `falhou`
  (`tests/integration/test_postgres_triage.py`).
- `PostgresMetricsReader.facts(since)`: `resolved` são os `resolved_at` dos incidentes no estado
  resolvido; `approved` são `status_at` e a avaliação dos post mortems aprovados ou encerrados;
  `answers` é a última resposta `seen_before_answered` de cada incidente (`DISTINCT ON`, pela
  ordem do log), descartada se anterior a `since`; tudo a partir de `since`, em ordem de data
  (`tests/integration/test_postgres_metrics.py`).
- `data` dos eventos, `labels` e `raw` voltam do banco como JSON: horários viram ISO 8601 e ids
  viram texto.

## 🧪 Testes

```bash
uv run pytest packages/grolla-core                      # unitários + integração
uv run pytest packages/grolla-core/tests/unit           # só unitários (sem rede)
```

| Pasta | O que tem | Rede |
|---|---|---|
| `tests/unit/` | Unitários; inclui a prova de que a rede está bloqueada | bloqueada |
| `tests/integration/` | Postgres real via Testcontainers (fixture `database_url`); o modelo `small` de embeddings real (`test_fastembed_model.py`, com paráfrase em português), que pula sem o modelo no cache de `GROLLA_RAG_CACHE` | liberada |
| `tests/contracts/` | Suíte de contrato de cada port (`ClockContract`, `IncidentRepositoryContract`, `NotificationOutboxContract`, `ChatNotifierContract`, `AlertIngressContract`, `TelemetryContract`, `ChangeHistoryContract`, `LanguageModelContract`, `AnalysisQueueContract`, `VerificationQueueContract`, `CloudInventoryContract`, `ChatReplyContract`, `WarRoomRepositoryContract`, `ChatQueueContract`, `CycleQueueContract`, `FollowUpModelContract`, `MemoryStoreContract`, `MemoryChunksContract`, `TextEmbedderContract`, `RepositoryDocumentsContract`, `PostMortemStoreContract`, `DocumentRendererContract`, `PostMortemModelContract`) | — |

Para rodar o teste do modelo real, baixe-o uma vez (0,22 GB) fora do repositório:

```bash
uv run python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='$HOME/.cache/grolla-models')"
GROLLA_RAG_CACHE=~/.cache/grolla-models uv run pytest packages/grolla-core/tests/integration/test_fastembed_model.py
```

Todo adapter e todo fake de um port herdam a suíte de contrato dele:

```python
class TestSystemClockContract(ClockContract):
    @pytest.fixture
    def clock(self) -> Clock:
        return SystemClock()
```

## 🗂 Layout

```
src/grolla/
  domain/          incidents.py · analysis.py · war_room.py · chat.py
  application/     clock.py · incidents.py · investigation.py · analysis.py · war_room.py · chat.py
  adapters/        system_clock/ · postgres/ (database, incidents, analysis, chat, follow_up, memory, post_mortem, metrics, migrate, migrations/) · signoz/ (ingress, telemetry) · teams/ · anthropic/ · azure_devops/ · aws/ · fastembed/
  bootstrap/       settings.py · team_config.py · container.py
  testing/         clock.py (FakeClock) · incidents.py · investigation.py (fakes) · memory.py (memória e embeddings) · builders.py
tests/             unit/ · integration/ · contracts/
```
