from dataclasses import dataclass

from grolla.adapters.anthropic import ClaudeLanguageModel, ModelSettings
from grolla.adapters.aws import AwsCloudInventory
from grolla.adapters.azure_devops import AzureDevOpsChangeHistory, RepositoryRef
from grolla.adapters.postgres import (
    PostgresAnalysisQueue,
    PostgresChatQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.adapters.signoz import SignozAlertIngress
from grolla.adapters.signoz.telemetry import SignozTelemetry
from grolla.adapters.system_clock import SystemClock
from grolla.adapters.teams import TeamsChatNotifier
from grolla.application.analysis import AnalysisPolicy, AnalysisQueue
from grolla.application.chat import ChatQueue
from grolla.application.clock import Clock
from grolla.application.incidents import (
    AlertIngress,
    ChatNotifier,
    IncidentRepository,
    IntakePolicy,
    NotificationOutbox,
)
from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    NoCloudInventory,
    Telemetry,
)
from grolla.bootstrap.settings import ApiSettings, ConfigError, WorkerSettings, load_settings
from grolla.bootstrap.team_config import StageConfig, TeamConfig, load_team_config


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
    config: TeamConfig
    clock: Clock
    database: PostgresDatabase
    outbox: NotificationOutbox
    notifier: ChatNotifier
    repository: IncidentRepository
    analysis_queue: AnalysisQueue
    chat_queue: ChatQueue
    model: LanguageModel
    telemetry: Telemetry
    changes: ChangeHistory
    cloud: CloudInventory
    analysis: AnalysisPolicy


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


def build_worker(settings: WorkerSettings, config: TeamConfig) -> WorkerContainer:
    """Aplica as migrações pendentes e liga os ports do worker aos adapters."""
    if config.llm is None or config.signoz is None or config.azure_devops is None:
        raise ConfigError(
            f"{settings.config_file}: o worker precisa das seções llm, signoz e azure_devops"
        )
    verification = config.llm.stages.get(STAGE_VERIFICATION)
    chat = config.llm.stages.get(STAGE_WARROOM_CHAT)
    stage = config.llm.stages.get(STAGE_INVESTIGATION)
    if stage is None:
        raise ConfigError(f"{settings.config_file}: falta llm.stages.{STAGE_INVESTIGATION}")
    changes = config.azure_devops
    migrate(settings.database_url)
    database = PostgresDatabase(settings.database_url)
    return WorkerContainer(
        settings=settings,
        config=config,
        clock=SystemClock(),
        database=database,
        outbox=PostgresNotificationOutbox(database),
        notifier=TeamsChatNotifier(settings.teams_webhook_url.get_secret_value()),
        repository=PostgresIncidentRepository(database),
        analysis_queue=PostgresAnalysisQueue(database),
        chat_queue=PostgresChatQueue(database),
        model=ClaudeLanguageModel(
            settings.anthropic_api_key.get_secret_value(),
            ModelSettings(model=stage.model, max_tokens=stage.max_tokens, effort=stage.effort),
            base_url=settings.anthropic_base_url,
            verification=_model_settings(verification),
            chat=_model_settings(chat),
        ),
        telemetry=SignozTelemetry(config.signoz.mcp_url, config.signoz.base_url),
        changes=AzureDevOpsChangeHistory(
            changes.base_url,
            settings.azure_devops_token.get_secret_value(),
            changes.projects,
            {
                service: RepositoryRef(ref.project, ref.repo)
                for service, ref in changes.service_repo_map.items()
            },
        ),
        cloud=NoCloudInventory()
        if config.cloud is None
        else AwsCloudInventory(config.cloud.regions, config.cloud.resource_map),
        analysis=config.analysis_policy(),
    )


def _model_settings(stage: StageConfig | None) -> ModelSettings | None:
    if stage is None:
        return None
    return ModelSettings(model=stage.model, max_tokens=stage.max_tokens, effort=stage.effort)


def prepare_api() -> ApiContainer:
    """Lê ambiente e YAML, valida tudo e monta a api; levanta ConfigError."""
    settings = load_settings(ApiSettings)
    return build_api(settings, load_team_config(settings.config_file))


def prepare_worker() -> WorkerContainer:
    """Lê ambiente e YAML, valida tudo e monta o worker; levanta ConfigError."""
    settings = load_settings(WorkerSettings)
    return build_worker(settings, load_team_config(settings.config_file))
