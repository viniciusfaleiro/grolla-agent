## Context

Depois do S1 existem o harness, o esqueleto da api e do worker e o e2e com fakes. O time piloto é a **Credenciadora**. O deploy na EC2 é manual e feito pelo usuário; a URL do Workflow do Teams e o acesso ao SigNoz existem. É a primeira change multiagente: domínio, persistência, integrações, web e e2e andam em paralelo a partir do commit de contrato.

## Goals / Non-Goals

**Goals:**
- Alertas reais da Credenciadora viram incidentes deduplicados e agrupados, com um card por problema no Teams e uma página por incidente.
- Registro completo e imutável de cada incidente, base da memória do agente.
- Pacote de deploy manual pronto para a EC2.

**Non-Goals:** ver a proposta (Spike.sh, war room interativo, grafo de traces, poller, LLM, SSE, auth, CI).

## Contratos

Escritos como código pelo agente pai no commit de contrato. Corpo das funções é `raise NotImplementedError` até o dono implementar.

### Domínio — `grolla.domain.incidents`

```python
class Severity(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"  # P0 é a pior


def worst(a: Severity, b: Severity) -> Severity: ...


class IncidentState(StrEnum):
    DETECTED = "detectado"
    INFORMED = "informado"


class AlertStatus(StrEnum):
    FIRING = "disparando"
    RESOLVED = "resolvido"


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    status: AlertStatus
    rule_name: str
    service: str  # "desconhecido" sem label de serviço
    labels: Mapping[str, str]
    summary: str
    description: str
    starts_at: datetime
    ends_at: datetime | None
    source_url: str | None
    raw: Mapping[str, Any]  # alerta bruto, para histórico


@dataclass(frozen=True)
class Incident:
    id: UUID
    title: str
    state: IncidentState
    severity: Severity
    services: tuple[str, ...]
    detected_at: datetime
    last_alert_at: datetime
    firing: frozenset[str]  # fingerprints disparando


class EventType(StrEnum):
    ALERT_RECEIVED = "alert_received"
    ALERT_GROUPED = "alert_grouped"
    ALERT_RESOLVED = "alert_resolved"
    SEVERITY_CHANGED = "severity_changed"
    STATE_CHANGED = "state_changed"
    INCIDENT_NORMALIZED = "incident_normalized"
    NOTIFICATION_QUEUED = "notification_queued"
    NOTIFICATION_DELIVERED = "notification_delivered"
    NOTIFICATION_FAILED = "notification_failed"


@dataclass(frozen=True)
class IncidentEvent:
    incident_id: UUID
    at: datetime
    type: EventType
    data: Mapping[str, Any]
    author: str = "sistema"


class NotificationKind(StrEnum):
    INCIDENT_OPENED = "incident_opened"
    SEVERITY_RAISED = "severity_raised"
    INCIDENT_NORMALIZED = "incident_normalized"


@dataclass(frozen=True)
class Notification:
    kind: NotificationKind
    incident_id: UUID
    title: str
    severity: Severity
    services: tuple[str, ...]
    summary: str
    evidence: str  # descrição do alerta que motivou
    source_url: str | None
    page_url: str


@dataclass(frozen=True)
class SeverityRule:
    match: Mapping[str, str]
    severity: Severity


@dataclass(frozen=True)
class SeverityPolicy:
    rules: tuple[SeverityRule, ...]
    default: Severity

    def classify(self, alert: Alert) -> tuple[Severity, int | None]: ...  # índice da regra ou None


@dataclass(frozen=True)
class Decision:
    incident: Incident | None  # None: alerta ignorado
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


def decide_firing(
    alert: Alert,
    candidates: Sequence[Incident],
    policy: SeverityPolicy,
    window: timedelta,
    now: datetime,
    page_url: Callable[[UUID], str],
    new_id: Callable[[], UUID],
) -> Decision: ...
def decide_resolved(
    alert: Alert, incident: Incident, now: datetime, page_url: Callable[[UUID], str]
) -> Decision: ...
```

### Ports — `grolla.application`

```python
class InvalidAlertPayload(Exception): ...


class NotificationFailed(Exception): ...


class AlertIngress(Protocol):  # adapter: grolla.adapters.signoz
    def authenticate(self, headers: Mapping[str, str]) -> bool: ...
    def parse(self, payload: Any) -> list[Alert]: ...  # InvalidAlertPayload


class ChatNotifier(Protocol):  # adapter: grolla.adapters.teams
    async def post(self, notification: Notification) -> None: ...  # NotificationFailed


class IncidentUnitOfWork(Protocol):  # adapter: grolla.adapters.postgres
    async def register_alert(self, alert: Alert) -> bool: ...  # False se já visto
    async def active_incidents(self, service: str, since: datetime) -> list[Incident]: ...
    async def incident_firing(self, fingerprint: str) -> Incident | None: ...
    async def save(self, incident: Incident) -> None: ...
    async def append(self, events: Sequence[IncidentEvent]) -> None: ...
    async def link_alert(self, alert: Alert, incident_id: UUID) -> None: ...
    async def enqueue(self, notification: Notification) -> None: ...


class IncidentRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[IncidentUnitOfWork]: ...
    async def list_incidents(self, limit: int) -> list[Incident]: ...
    async def get_incident(self, incident_id: UUID) -> Incident | None: ...
    async def alerts_of(self, incident_id: UUID) -> list[Alert]: ...
    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]: ...


@dataclass(frozen=True)
class OutboxItem:
    id: int
    notification: Notification
    attempts: int


class NotificationOutbox(Protocol):  # adapter: grolla.adapters.postgres
    async def due(self, now: datetime, limit: int) -> list[OutboxItem]: ...  # ordem de criação
    async def delivered(self, item: OutboxItem, at: datetime) -> None: ...  # + evento
    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None: ...
    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None: ...  # + evento
```

### Casos de uso — `grolla.application`

```python
@dataclass(frozen=True)
class IntakePolicy:
    label_filter: Mapping[str, str]
    severity: SeverityPolicy
    window: timedelta
    page_url: Callable[[UUID], str]


async def receive_alerts(
    alerts: Sequence[Alert], repository: IncidentRepository, policy: IntakePolicy, clock: Clock
) -> None: ...
async def deliver_notifications(
    outbox: NotificationOutbox,
    notifier: ChatNotifier,
    clock: Clock,
    max_attempts: int = 8,
    base_delay: timedelta = timedelta(seconds=5),
) -> int: ...
```

Cada alerta é processado numa transação própria (`register_alert` → decisão → `save`/`append`/`link_alert`/`enqueue`). Na entrega, uma falha num card de um incidente adia os cards seguintes do mesmo incidente na rodada, para manter a ordem.

### Configuração — `grolla.bootstrap`

```yaml
# GROLLA_CONFIG_FILE
team: credenciadora
page_base_url: https://grolla-credenciadora.interno
alert_filter: { team: credenciadora }
grouping_window_minutes: 15
default_severity: P3
severity_rules:
  - match: { alertname: "Queda de aprovação" }
    severity: P0
  - match: { severity: critical }
    severity: P1
```

Ambiente: `GROLLA_DATABASE_URL`, `GROLLA_CONFIG_FILE`, `GROLLA_LOG_LEVEL`; na api `GROLLA_ALERTS_WEBHOOK_USER` e `GROLLA_ALERTS_WEBHOOK_PASSWORD`; no worker `GROLLA_TEAMS_WEBHOOK_URL`. O `bootstrap` ganha `prepare_api()` e `prepare_worker()`, cada um exigindo só os segredos do próprio processo.

### HTTP — `grolla-api`

| Método e caminho | Resposta |
|---|---|
| `POST /webhooks/alerts` | 202 aceito · 401 sem credencial · 422 payload inválido |
| `GET /incidents` | HTML: lista |
| `GET /incidents/{id}` | HTML: página do incidente · 404 |
| `GET /health/worker` | 200 / 503 com `{"last_heartbeat": ...}` |

### Schema — migração `0002`

- `incidents` (`id uuid pk`, `title`, `state`, `severity`, `services text[]`, `detected_at`, `last_alert_at`, `firing text[]`).
- `incident_events` (`id bigserial`, `incident_id fk`, `at`, `type`, `author`, `data jsonb`), com trigger que recusa `UPDATE` e `DELETE`: somente inserção imposta pelo banco.
- `alerts` (`id bigserial`, `incident_id fk null`, `fingerprint`, `status`, `starts_at`, `ends_at`, `rule_name`, `service`, `labels jsonb`, `summary`, `description`, `source_url`, `raw jsonb`, `received_at`), `UNIQUE (fingerprint, starts_at, status)` para a deduplicação.
- `outbox` (`id bigserial`, `incident_id`, `notification jsonb`, `created_at`, `attempts`, `next_attempt_at`, `delivered_at`, `failed_at`, `last_error`).

### Fakes e e2e

- O Teams no e2e é a rota `POST /teams/webhook` do `grolla-fakes`, declarada no roteiro do cenário (202). O worker do e2e aponta `GROLLA_TEAMS_WEBHOOK_URL` para ela.
- O runner ganha a ação `expect_fake_requests` (caminho, quantidade, trecho do corpo) para conferir os cards.
- O papel do SigNoz no e2e é do próprio runner, que chama o webhook da api com payloads de fixture.

## Decisions

1. **Decisão pura no domínio.** `decide_firing`/`decide_resolved` recebem tudo por parâmetro (candidatos, política, horário, gerador de id) e devolvem incidente, eventos e notificações. Os casos de uso só buscam e gravam. Testável com `FakeClock` e sem banco; é onde está a cobertura de 90%.
2. **Agrupamento só por serviço no S2.** *Mais simples considerada e escolhida.* Dependência pelo grafo de traces exige o port `Telemetry` (S4).
3. **Deduplicação pelo banco.** `UNIQUE (fingerprint, starts_at, status)` e `register_alert` devolvendo se é novo. *Mais simples considerada:* cache em memória; descartada porque não sobrevive a restart e não serve com dois processos.
4. **Imutabilidade imposta por trigger.** *Mais simples considerada:* só convenção no código; descartada porque o log de eventos é a base da auditoria e da memória do agente.
5. **Outbox no Postgres + job periódico de 5 s, uma entrega por vez** (`queueing_lock`). *Mais simples considerada:* postar no Teams direto na requisição do webhook; descartada porque uma falha do Teams perderia o card ou travaria o webhook. Com um job só, não precisa de `SKIP LOCKED`.
6. **Autenticação HTTP Basic no webhook**, que o canal de webhook do SigNoz suporta. Comparação em tempo constante.
7. **Páginas server-side com Jinja2, sem JS.** *Mais simples considerada e escolhida*; HTMX e SSE entram quando houver interação (S3).
8. **`httpx2` como cliente HTTP dos adapters**, mesmo pacote que o TestClient já usa; um cliente só no workspace.
9. **Saúde do worker pela api** (`/health/worker`). *Mais simples considerada e escolhida:* qualquer checagem de URL do SigNoz ou da EC2 pega o worker parado, sem instrumentação OpenTelemetry neste slice.
10. **Deploy manual com o próprio compose**: `deploy/compose.yml` constrói as imagens na EC2 a partir do repositório. *Mais simples considerada e escolhida*; ECR e CI quando o deploy deixar de ser manual.

## Divisão de trabalho

| Papel | Entrega |
|---|---|
| agente pai | commit de contrato, `bootstrap`, dependências, compose do e2e, `deploy/`, integração |
| `grolla-dominio` | `decide_firing`, `decide_resolved`, `SeverityPolicy`, `worst`, `receive_alerts`, `deliver_notifications`, fakes dos ports e suítes de contrato |
| `grolla-persistencia` | `IncidentRepository`, `IncidentUnitOfWork`, `NotificationOutbox` sobre a migração 0002 |
| `grolla-integracoes` | `grolla.adapters.signoz` (`AlertIngress`), `grolla.adapters.teams` (`ChatNotifier` + Adaptive Card), job de entrega no `grolla-worker` |
| `grolla-web` | webhook, páginas e `/health/worker` no `grolla-api` |
| `grolla-e2e` | ação `expect_fake_requests` e cenários |

## Risks / Trade-offs

- [Formato exato do webhook do SigNoz diferente do esperado] → o adapter é testado com fixture de payload real; o agente de integrações pega um payload real do SigNoz da Credenciadora antes de fechar.
- [Labels de serviço e time ausentes nos alertas] → `desconhecido` como serviço e filtro configurável; checar os labels reais antes do aceite (questão em aberto da spec).
- [Página sem autenticação] → só rede interna, como define a fase 1.
- [Card perdido se o worker ficar parado muito tempo] → a outbox guarda; `/health/worker` denuncia o worker parado.
- [Divergência de contrato entre agentes] → contrato em código antes de começar; mudança combinada por mensagem e registrada aqui.

## Migration Plan

1. Merge na `master` com `make check` e `make e2e` verdes.
2. Deploy manual na EC2 seguindo `deploy/README.md` (o usuário executa).
3. Configurar no SigNoz o canal de webhook apontando para `/webhooks/alerts` com as credenciais, nas regras da Credenciadora.
4. Aceite: alertas reais viram incidentes no canal, sem duplicados.
5. Rollback: desligar o canal no SigNoz; o banco guarda o que já entrou.

## Open Questions

- Labels reais dos alertas da Credenciadora (nome do label de serviço e de time).
- Regras de severidade iniciais da Credenciadora: entram como exemplo e o time ajusta no YAML.
