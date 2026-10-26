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
| **Fila do chat do war room** | `ChatQueue`: a pergunta é a fila (`SKIP LOCKED`), perguntas presas, resposta numa transação (mensagem do agente, desfecho na pergunta, eventos e `llm_usage`) e tokens já gastos no chat do incidente | `grolla.adapters.postgres.PostgresChatQueue` |
| **Migrações** | Alembic sob advisory lock; idempotente e seguro em paralelo | `grolla.adapters.postgres.migrate` |
| **Entrada de alertas (SigNoz)** | `AlertIngress`: HTTP Basic em tempo constante; payload do webhook (formato Alertmanager) vira `Alert` neutro com o bruto em `raw`; serviço do primeiro label de `service_labels` ou `desconhecido` | `grolla.adapters.signoz.SignozAlertIngress` |
| **Cards no chat (Teams)** | `ChatNotifier`: Adaptive Card por tipo de notificação postado no Workflow do canal, timeout de 10 s; HTTP ≥ 400 ou erro de rede viram `NotificationFailedError` (sem expor a URL). O incidente novo é o card "Estou investigando", que lista o que vai ser checado. O card de análise traz o resumo e as seções "O que avaliei", "Hipóteses" (com confiança e evidência) e "Plano proposto", além dos botões "Abrir incidente" e "Ver no SigNoz". A análise parcial ("Análise parcial") diz o motivo logo abaixo do título; a com hipóteses avisa "Estou validando as hipóteses; a causa raiz vem em seguida.". O card da validação abre com "Causa raiz · P1", "Causa raiz não confirmada · P1" ou "Validação parcial · P1" (com "Motivo: …" logo abaixo) e traz as seções "Causa raiz", "Hipóteses" (`- <hipótese>: confirmada/rejeitada/inconclusiva — <justificativa>`), "Plano de ação" (`- <ação> (<alvo>) → <efeito>`, com link quando houver) e "O que faltou", só com o botão "Abrir incidente". O card "Incidente normalizado" fecha a história: duração ("Normalizado: durou 32 min") e o desfecho da validação (causa raiz e "Plano de ação sugerido", causa não confirmada com o que faltou, ou validação em andamento). As ações humanas do war room ganham cards curtos com o autor: "IC assumiu · P1", "Severidade alterada · P3" (com o motivo, se houver), "Incidente mitigado · P1", "Incidente descartado · P1" (com "Motivo: …") e "Incidente encerrado · P1", com "Encerrado por Ana · durou 35 min", o motivo, as ações registradas pelo time e a seção "Causa raiz" quando a validação a confirmou. As mensagens do chat não vão para o canal | `grolla.adapters.teams.TeamsChatNotifier` |
| **Modelo de linguagem (Anthropic)** | `LanguageModel`: laço de ferramentas manual sobre o `Toolbox` (tool_use em paralelo), resultado final por structured outputs (`output_config.format` com o schema do rascunho), thinking adaptativo, `effort` e modelo do YAML e `cache_control` de 1 h no system e nas tools. Faz `budget.spend` a cada resposta, com o modelo que respondeu. Usa 2 retentativas e timeout de 60 s por requisição. Erro da API ou recusa viram `LanguageModelError`; JSON fora do schema ou resposta truncada viram `InvalidAnalysisError`. A validação (`verify`) usa o mesmo laço com `VERIFICATION_PROMPT`, o schema do `VerificationDraft` e modelo e `effort` de `llm.stages.verification` (sem ela, os da análise), com o uso na etapa `verification`; plano que `check_plan` recusa ganha uma única mensagem de correção e, se voltar, vira `InvalidAnalysisError`. A resposta do chat do war room (`reply`) usa o mesmo laço com `CHAT_PROMPT`, uma mensagem com o contexto do incidente e, por último, `Pergunta: <pergunta>`, o schema `{"reply": string}` e modelo e `effort` de `llm.stages.warroom_chat` (sem ela, os da análise), com o uso na etapa `warroom_chat`; texto vazio ou fora do schema vira `InvalidAnalysisError` | `grolla.adapters.anthropic.ClaudeLanguageModel` |
| **Telemetria (MCP do SigNoz)** | `Telemetry` pelo `signoz-mcp-server` (streamable HTTP, sem chave: ela fica no container do servidor). Lista fechada de leitura (`signoz_aggregate_traces`, `signoz_search_logs`, `signoz_search_traces`, `signoz_get_service_top_operations`); outra ferramenta é recusada sem chamar o servidor. Sinais neutros viram agregações sobre os spans (`taxa_de_erro` = spans com erro ÷ total, `latencia_p99` = p99 de `duration_nano`, `vazao` = `rate()`), em pontos de 1 min, com o resumo dizendo início, fim, máximo e "Mudou às HH:MM, de X para Y" (o primeiro ponto a mais de 3x da média dos 5 primeiros, ou a mais de 5 pontos percentuais na taxa de erro), e só os últimos 10 pontos. Logs são WARN e acima (`severity_number >= 13`), agrupados com números longos, IPs, `0x…` e UUIDs normalizados, nas 5 mensagens mais frequentes (`31x [ERROR] …`, só a primeira linha). Resumo compacto em português com link para o SigNoz; timeout de 20 s por chamada; erro, timeout ou resposta fora do formato viram `ToolUnavailableError` | `grolla.adapters.signoz.telemetry.SignozTelemetry` |
| **Deploys (Azure DevOps)** | `ChangeHistory` pela API REST (`_apis/build/builds?repositoryId=…&repositoryType=TfsGit&minTime=…`), PAT somente leitura em Basic. Repositório pelo mapa (exceções) ou pelo nome do serviço nos `projects` (`_apis/git/repositories/{nome}`), com cache em memória; sem repositório, responde "serviço sem repositório mapeado". Até 10 builds com horário, versão, branch, primeira linha da mensagem do commit (`triggerInfo["ci.message"]` ou `sourceVersionMessage`; sem elas, a API de commits do repositório, só para os builds listados, e falha nessa busca só omite a mensagem), autor e resultado, e link do pipeline; HTTP ≠ 200, rede ou formato inesperado viram `ToolUnavailableError` | `grolla.adapters.azure_devops.AzureDevOpsChangeHistory` |
| **Nuvem (AWS)** | `CloudInventory` com boto3 em `asyncio.to_thread` e lista fechada de leitura (`tag:GetResources`, `rds:DescribeDBInstances`, `ec2:DescribeInstances`, `elbv2:DescribeLoadBalancers`, `cloudtrail:LookupEvents`, `cloudwatch:DescribeAlarms`). Recursos pela tag `service=<nome>` em cada região mais o `resource_map` (ARNs; identificador solto fica na primeira região), com o estado de banco RDS, máquina EC2 e balanceador ELBv2 e o link do console. Mudanças pelo CloudTrail por `ResourceName`, sem leituras, `HH:MM EventName em <recurso> por <usuário>` (até 20, com o aviso do atraso de 5 min). Alarmes em `ALARM` cujas dimensões citam os recursos. Credencial, permissão ou rede viram `ToolUnavailableError` | `grolla.adapters.aws.AwsCloudInventory` |
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

### Repositórios

- `PostgresIncidentRepository.transaction()` abre **uma transação** e entrega a unidade de
  trabalho (`register_alert`, `active_incidents`, `incident_firing`, `save`, `append`,
  `link_alert`, `enqueue`, `incident_for_update`, `add_message`, `request_analysis`): commit ao sair sem erro, rollback em exceção. Assim o evento, a
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
| `tests/integration/` | Postgres real via Testcontainers (fixture `database_url`) | liberada |
| `tests/contracts/` | Suíte de contrato de cada port (`ClockContract`, `IncidentRepositoryContract`, `NotificationOutboxContract`, `ChatNotifierContract`, `AlertIngressContract`, `TelemetryContract`, `ChangeHistoryContract`, `LanguageModelContract`, `AnalysisQueueContract`, `VerificationQueueContract`, `CloudInventoryContract`, `ChatReplyContract`, `WarRoomRepositoryContract`, `ChatQueueContract`) | — |

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
  adapters/        system_clock/ · postgres/ (database, incidents, analysis, chat, migrate, migrations/) · signoz/ (ingress, telemetry) · teams/ · anthropic/ · azure_devops/ · aws/
  bootstrap/       settings.py · team_config.py · container.py
  testing/         clock.py (FakeClock) · incidents.py · investigation.py (fakes) · builders.py
tests/             unit/ · integration/ · contracts/
```
