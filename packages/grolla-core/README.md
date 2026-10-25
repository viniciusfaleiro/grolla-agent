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
| **Receber alertas** | Filtro por time, deduplicação e uma transação por alerta | `grolla.application.incidents.receive_alerts` |
| **Entregar cards** | Outbox em ordem por incidente, espera dobrando a cada falha, desistência no limite | `grolla.application.incidents.deliver_notifications` |
| **Ports de incidentes** | `AlertIngress`, `ChatNotifier`, `IncidentRepository`/`IncidentUnitOfWork`, `NotificationOutbox` | `grolla.application.incidents` |
| **Fakes de incidentes** | Repositório e outbox em memória, canal de chat que grava e falha sob comando, entrada de alertas | `grolla.testing.incidents` · `grolla.testing.builders` |
| **Postgres** | Engine async, ping, heartbeats | `grolla.adapters.postgres.PostgresDatabase` |
| **Repositório de incidentes** | Unidade de trabalho transacional, deduplicação de alertas, log de eventos e projeção | `grolla.adapters.postgres.PostgresIncidentRepository` |
| **Outbox de notificações** | Fila de cards com retentativa; entrega e desistência viram evento no incidente | `grolla.adapters.postgres.PostgresNotificationOutbox` |
| **Migrações** | Alembic sob advisory lock; idempotente e seguro em paralelo | `grolla.adapters.postgres.migrate` |
| **Entrada de alertas (SigNoz)** | `AlertIngress`: HTTP Basic em tempo constante; payload do webhook (formato Alertmanager) vira `Alert` neutro com o bruto em `raw`; serviço do primeiro label de `service_labels` ou `desconhecido` | `grolla.adapters.signoz.SignozAlertIngress` |
| **Cards no chat (Teams)** | `ChatNotifier`: Adaptive Card por tipo de notificação postado no Workflow do canal, timeout de 10 s; HTTP ≥ 400 ou erro de rede viram `NotificationFailedError` (sem expor a URL) | `grolla.adapters.teams.TeamsChatNotifier` |
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
- `migrate(url)` segura `pg_advisory_xact_lock` durante o upgrade: api e worker podem subir juntos.

### Tabelas do S2

| Tabela | O que guarda | Regra imposta pelo banco |
|---|---|---|
| `incidents` | Projeção do estado atual de cada incidente (`services` e `firing` em `text[]`) | atualizada na mesma transação dos eventos |
| `incident_events` | Log de eventos do incidente (`data` em `jsonb`) | **só inserção**: trigger recusa `UPDATE`, `DELETE` e `TRUNCATE` |
| `alerts` | Cada alerta recebido, com labels e payload bruto (`jsonb`) e o incidente a que foi ligado | `UNIQUE (fingerprint, starts_at, status)` deduplica reenvios |
| `outbox` | Notificações a entregar (`notification` em `jsonb`), tentativas e próximo horário | — |

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
| `tests/contracts/` | Suíte de contrato de cada port (`ClockContract`, `IncidentRepositoryContract`, `NotificationOutboxContract`, `ChatNotifierContract`, `AlertIngressContract`) | — |

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
  domain/          incidents.py
  application/     clock.py · incidents.py
  adapters/        system_clock/ · postgres/ (database, incidents, migrate, migrations/) ·
                   signoz/ (webhook + telemetry.py, cliente MCP) · azure_devops/ · teams/
  bootstrap/       settings.py · team_config.py · container.py
  testing/         clock.py (FakeClock) · incidents.py (fakes) · builders.py
tests/             unit/ · integration/ · contracts/
```
