from dataclasses import dataclass

from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.adapters.signoz import SignozAlertIngress
from grolla.adapters.system_clock import SystemClock
from grolla.adapters.teams import TeamsChatNotifier
from grolla.application.clock import Clock
from grolla.application.incidents import (
    AlertIngress,
    ChatNotifier,
    IncidentRepository,
    IntakePolicy,
    NotificationOutbox,
)
from grolla.bootstrap.settings import ApiSettings, WorkerSettings, load_settings
from grolla.bootstrap.team_config import TeamConfig, load_team_config


@dataclass(frozen=True)
class ApiContainer:
    """Dependências do grolla-api."""

    settings: ApiSettings
    config: TeamConfig
    clock: Clock
    database: PostgresDatabase
    ingress: AlertIngress
    repository: IncidentRepository
    intake: IntakePolicy


@dataclass(frozen=True)
class WorkerContainer:
    """Dependências do grolla-worker."""

    settings: WorkerSettings
    clock: Clock
    database: PostgresDatabase
    outbox: NotificationOutbox
    notifier: ChatNotifier


def build_api(settings: ApiSettings, config: TeamConfig) -> ApiContainer:
    """Aplica as migrações pendentes e liga os ports da api aos adapters."""
    migrate(settings.database_url)
    database = PostgresDatabase(settings.database_url)
    return ApiContainer(
        settings=settings,
        config=config,
        clock=SystemClock(),
        database=database,
        ingress=SignozAlertIngress(
            settings.alerts_webhook_user,
            settings.alerts_webhook_password.get_secret_value(),
            config.service_labels,
        ),
        repository=PostgresIncidentRepository(database),
        intake=config.intake_policy(),
    )


def build_worker(settings: WorkerSettings) -> WorkerContainer:
    """Aplica as migrações pendentes e liga os ports do worker aos adapters."""
    migrate(settings.database_url)
    database = PostgresDatabase(settings.database_url)
    return WorkerContainer(
        settings=settings,
        clock=SystemClock(),
        database=database,
        outbox=PostgresNotificationOutbox(database),
        notifier=TeamsChatNotifier(settings.teams_webhook_url.get_secret_value()),
    )


def prepare_api() -> ApiContainer:
    """Lê ambiente e YAML, valida tudo e monta a api; levanta ConfigError."""
    settings = load_settings(ApiSettings)
    return build_api(settings, load_team_config(settings.config_file))


def prepare_worker() -> WorkerContainer:
    """Lê o ambiente, valida e monta o worker; levanta ConfigError."""
    return build_worker(load_settings(WorkerSettings))
