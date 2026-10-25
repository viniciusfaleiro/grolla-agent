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
| **Postgres** | Engine async, ping, heartbeats | `grolla.adapters.postgres.PostgresDatabase` |
| **Repositório de incidentes** | Unidade de trabalho transacional, deduplicação de alertas, log de eventos e projeção | `grolla.adapters.postgres.PostgresIncidentRepository` |
| **Outbox de notificações** | Fila de cards com retentativa; entrega e desistência viram evento no incidente | `grolla.adapters.postgres.PostgresNotificationOutbox` |
| **Migrações** | Alembic sob advisory lock; idempotente e seguro em paralelo | `grolla.adapters.postgres.migrate` |
| **Configuração** | Variáveis `GROLLA_*` validadas; erro nomeia a variável | `grolla.bootstrap.load_settings` |
| **Composição** | Migra e monta as dependências de um processo | `grolla.bootstrap.prepare` |

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
| `tests/contracts/` | Suíte de contrato de cada port (`ClockContract`) | — |

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
  domain/          (vazio no S1)
  application/     clock.py
  adapters/        system_clock/ · postgres/ (database, incidents, migrate, migrations/)
  bootstrap/       settings.py · container.py
  testing/         clock.py (FakeClock)
tests/             unit/ · integration/ · contracts/
```
