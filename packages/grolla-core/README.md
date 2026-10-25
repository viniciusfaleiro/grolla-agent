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
| **Incidentes (domínio)** | Severidade por regras, agrupamento por serviço na janela, estados Detectado/Informado, eventos e cards — decisões puras | `grolla.domain.incidents.decide_firing` · `decide_resolved` |
| **Receber alertas** | Filtro por time, deduplicação e uma transação por alerta; incidente novo grava o pedido de análise (`analysis_requested`) na mesma transação | `grolla.application.incidents.receive_alerts` |
| **Análise (domínio)** | Análise concluída (hipóteses por confiança) ou parcial (motivo e o que foi avaliado); texto de cada motivo para card e página | `grolla.domain.analysis.complete` · `partial` · `PARTIAL_REASON_TEXT` |
| **Ferramentas da investigação** | `query_metric`, `search_logs`, `find_traces` e `recent_deploys` com JSON Schema fechado e janela do `lookback`; falha vira avaliação "não foi possível consultar", nunca exceção | `grolla.application.investigation.Toolbox` |
| **Orçamento e prompt** | Limite de tokens por análise (entrada, saída e cache); roteiro curto neutro (impacto, deploys, traces, logs) e mensagem montada do incidente | `TokenBudget` · `SYSTEM_PROMPT` · `brief_text` |
| **Rodar a análise** | Reivindica o pedido, investiga sob timeout, converte toda falha em parcial e grava análise, evento, card `analysis_ready` e uso do modelo numa transação; o reaper encerra como "interrompida" o que passou do timeout + 2 min | `grolla.application.analysis.run_next_analysis` · `reap_stuck_analyses` |
| **Ports da investigação** | `Telemetry`, `ChangeHistory`, `LanguageModel`, `AnalysisQueue` | `grolla.application.investigation` · `grolla.application.analysis` |
| **Fakes da investigação** | Telemetria e histórico roteirizados, modelo que chama ferramentas e devolve, falha ou demora, fila de análises em memória | `grolla.testing.investigation` · `InMemoryAnalysisQueue` · `make_analysis` |
| **Entregar cards** | Outbox em ordem por incidente, espera dobrando a cada falha, desistência no limite | `grolla.application.incidents.deliver_notifications` |
| **Ports de incidentes** | `AlertIngress`, `ChatNotifier`, `IncidentRepository`/`IncidentUnitOfWork`, `NotificationOutbox` | `grolla.application.incidents` |
| **Fakes de incidentes** | Repositório e outbox em memória, canal de chat que grava e falha sob comando, entrada de alertas | `grolla.testing.incidents` · `grolla.testing.builders` |
| **Postgres** | Engine async, ping, heartbeats | `grolla.adapters.postgres.PostgresDatabase` |
| **Repositório de incidentes** | Unidade de trabalho transacional, deduplicação de alertas, log de eventos e projeção | `grolla.adapters.postgres.PostgresIncidentRepository` |
| **Outbox de notificações** | Fila de cards com retentativa; entrega e desistência viram evento no incidente | `grolla.adapters.postgres.PostgresNotificationOutbox` |
| **Fila de análises** | `AnalysisQueue`: reivindicação com `SKIP LOCKED`, análises presas e encerramento numa transação (análise, eventos, card e `llm_usage`) | `grolla.adapters.postgres.PostgresAnalysisQueue` |
| **Migrações** | Alembic sob advisory lock; idempotente e seguro em paralelo | `grolla.adapters.postgres.migrate` |
| **Entrada de alertas (SigNoz)** | `AlertIngress`: HTTP Basic em tempo constante; payload do webhook (formato Alertmanager) vira `Alert` neutro com o bruto em `raw`; serviço do primeiro label de `service_labels` ou `desconhecido` | `grolla.adapters.signoz.SignozAlertIngress` |
| **Cards no chat (Teams)** | `ChatNotifier`: Adaptive Card por tipo de notificação postado no Workflow do canal, timeout de 10 s; HTTP ≥ 400 ou erro de rede viram `NotificationFailedError` (sem expor a URL). O incidente novo é o card "Estou investigando", que lista o que vai ser checado. O card de análise traz o resumo e as seções "O que avaliei", "Hipóteses" (com confiança e evidência) e "Plano proposto", além dos botões "Abrir incidente" e "Ver no SigNoz". A análise parcial ("Análise parcial") diz o motivo logo abaixo do título | `grolla.adapters.teams.TeamsChatNotifier` |
| **Modelo de linguagem (Anthropic)** | `LanguageModel`: laço de ferramentas manual sobre o `Toolbox` (tool_use em paralelo), resultado final por structured outputs (`output_config.format` com o schema do rascunho), thinking adaptativo, `effort` e modelo do YAML e `cache_control` de 1 h no system e nas tools. Faz `budget.spend` a cada resposta, com o modelo que respondeu. Usa 2 retentativas e timeout de 60 s por requisição. Erro da API ou recusa viram `LanguageModelError`; JSON fora do schema ou resposta truncada viram `InvalidAnalysisError` | `grolla.adapters.anthropic.ClaudeLanguageModel` |
| **Telemetria (MCP do SigNoz)** | `Telemetry` pelo `signoz-mcp-server` (streamable HTTP, sem chave: ela fica no container do servidor). Lista fechada de leitura (`signoz_aggregate_traces`, `signoz_search_logs`, `signoz_search_traces`, `signoz_get_service_top_operations`); outra ferramenta é recusada sem chamar o servidor. Sinais neutros viram agregações sobre os spans (`taxa_de_erro` = spans com erro ÷ total, `latencia_p99` = p99 de `duration_nano`, `vazao` = `rate()`), em 6 pontos na janela; logs de erro são `severity_number >= 17`. Resumo compacto em português com link para o SigNoz; timeout de 20 s por chamada; erro, timeout ou resposta fora do formato viram `ToolUnavailableError` | `grolla.adapters.signoz.telemetry.SignozTelemetry` |
| **Deploys (Azure DevOps)** | `ChangeHistory` pela API REST (`_apis/build/builds?repositoryId=…&repositoryType=TfsGit&minTime=…`), PAT somente leitura em Basic. Repositório pelo mapa (exceções) ou pelo nome do serviço nos `projects` (`_apis/git/repositories/{nome}`), com cache em memória; sem repositório, responde "serviço sem repositório mapeado". Até 10 builds com horário, versão, branch, autor e resultado, e link do pipeline; HTTP ≠ 200, rede ou formato inesperado viram `ToolUnavailableError` | `grolla.adapters.azure_devops.AzureDevOpsChangeHistory` |
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

### Repositórios

- `PostgresIncidentRepository.transaction()` abre **uma transação** e entrega a unidade de
  trabalho (`register_alert`, `active_incidents`, `incident_firing`, `save`, `append`,
  `link_alert`, `enqueue`): commit ao sair sem erro, rollback em exceção. Assim o evento, a
  projeção e o card na outbox são gravados juntos ou nada é gravado.
- `register_alert` é um `INSERT … ON CONFLICT DO NOTHING RETURNING id`: devolve `False` para um
  reenvio, mesmo com dois processos.
- `active_incidents` segura um advisory lock por serviço e `FOR UPDATE` nos candidatos, e
  `incident_firing` trava o incidente: webhooks simultâneos do mesmo serviço não abrem dois
  incidentes, e resoluções simultâneas não perdem a atualização de `firing`
  (`tests/integration/test_postgres_concurrency.py`).
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
| `tests/contracts/` | Suíte de contrato de cada port (`ClockContract`, `IncidentRepositoryContract`, `NotificationOutboxContract`, `ChatNotifierContract`, `AlertIngressContract`, `TelemetryContract`, `ChangeHistoryContract`, `LanguageModelContract`, `AnalysisQueueContract`) | — |

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
  domain/          incidents.py · analysis.py
  application/     clock.py · incidents.py · investigation.py · analysis.py
  adapters/        system_clock/ · postgres/ (database, incidents, analysis, migrate, migrations/) · signoz/ (ingress, telemetry) · teams/ · anthropic/ · azure_devops/
  bootstrap/       settings.py · team_config.py · container.py
  testing/         clock.py (FakeClock) · incidents.py · investigation.py (fakes) · builders.py
tests/             unit/ · integration/ · contracts/
```
