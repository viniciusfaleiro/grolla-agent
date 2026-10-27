from dataclasses import dataclass, field

from grolla.adapters.anthropic import ClaudeLanguageModel, ModelSettings
from grolla.adapters.aws import AwsCloudInventory
from grolla.adapters.azure_devops import AzureDevOpsChangeHistory, RepositoryRef
from grolla.adapters.fastembed import FastembedTextEmbedder
from grolla.adapters.fpdf import FpdfDocumentRenderer
from grolla.adapters.postgres import (
    PostgresAnalysisQueue,
    PostgresChatQueue,
    PostgresConsoleStore,
    PostgresCycleQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresLearningQueue,
    PostgresMemoryStore,
    PostgresMetricsReader,
    PostgresNotificationOutbox,
    PostgresPostMortemStore,
    PostgresStudyQueue,
    PostgresTriageQueue,
    migrate,
)
from grolla.adapters.signoz import SignozAlertIngress
from grolla.adapters.signoz.telemetry import SignozTelemetry
from grolla.adapters.system_clock import SystemClock
from grolla.adapters.teams import TeamsChatNotifier
from grolla.application.analysis import AnalysisPolicy, AnalysisQueue
from grolla.application.chat import ChatQueue
from grolla.application.clock import Clock
from grolla.application.console import ConsolePolicy, ConsoleStore
from grolla.application.follow_up import CycleQueue, FollowUpLimits, ImpactPolicy
from grolla.application.incidents import (
    AlertIngress,
    ChatNotifier,
    IncidentRepository,
    IntakePolicy,
    NotificationOutbox,
)
from grolla.application.investigation import (
    STAGE_CONSOLE,
    STAGE_FOLLOW_UP,
    STAGE_INVESTIGATION,
    STAGE_LEARNING,
    STAGE_POST_MORTEM,
    STAGE_STUDY,
    STAGE_TRIAGE,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    NoCloudInventory,
    Telemetry,
)
from grolla.application.memory import LearningQueue, MemoryPolicy, StudyQueue
from grolla.application.memory_store import EmbeddingError, MemoryStore, TextEmbedder
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import DocumentRenderer, PostMortemPolicy, PostMortemStore
from grolla.application.triage import TriagePolicy, TriageQueue
from grolla.bootstrap.settings import ApiSettings, ConfigError, WorkerSettings, load_settings
from grolla.bootstrap.team_config import StageConfig, TeamConfig, load_team_config
from grolla.domain.follow_up import FollowUpPolicy


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
    memory: MemoryStore
    studies: StudyQueue
    learning_queue: LearningQueue  # aprendizados falhos na página da memória
    impact_policy: ImpactPolicy
    post_mortems: PostMortemStore
    renderer: DocumentRenderer
    metrics: MetricsReader
    console: ConsoleStore
    console_policy: ConsolePolicy | None = None  # None: console desligado


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
    memory: MemoryStore
    learning_queue: LearningQueue
    studies: StudyQueue
    memory_policy: MemoryPolicy
    embedder: TextEmbedder | None = None  # só com `memory.rag`
    cycle_queue: CycleQueue | None = None  # só com `follow_up`
    follow_up_policy: FollowUpPolicy | None = None
    follow_up_limits: FollowUpLimits = field(default_factory=FollowUpLimits)
    impact_policy: ImpactPolicy = field(default_factory=ImpactPolicy)
    post_mortems: PostMortemStore | None = None
    post_mortem_policy: PostMortemPolicy = field(default_factory=PostMortemPolicy)
    triage_queue: TriageQueue | None = None  # só com `noise`
    triage_policy: TriagePolicy | None = None
    console: ConsoleStore | None = None  # só com `console`
    console_policy: ConsolePolicy | None = None


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
        memory=PostgresMemoryStore(database),
        studies=PostgresStudyQueue(database),
        learning_queue=PostgresLearningQueue(database),
        impact_policy=config.impact_policy(),
        post_mortems=PostgresPostMortemStore(database),
        renderer=FpdfDocumentRenderer(),
        metrics=PostgresMetricsReader(database),
        console=PostgresConsoleStore(database),
        console_policy=config.console_policy(),
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
            learning=_model_settings(config.llm.stages.get(STAGE_LEARNING)),
            study=_model_settings(config.llm.stages.get(STAGE_STUDY)),
            follow_up=_model_settings(config.llm.stages.get(STAGE_FOLLOW_UP)),
            post_mortem=_model_settings(config.llm.stages.get(STAGE_POST_MORTEM)),
            triage=_model_settings(config.llm.stages.get(STAGE_TRIAGE)),
            console=_model_settings(config.llm.stages.get(STAGE_CONSOLE)),
        ),
        telemetry=SignozTelemetry(
            config.signoz.mcp_url, config.signoz.base_url, service_labels=config.service_labels
        ),
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
        memory=PostgresMemoryStore(database),
        learning_queue=PostgresLearningQueue(database),
        studies=PostgresStudyQueue(database),
        memory_policy=config.memory_policy(),
        embedder=_embedder(settings, config),
        cycle_queue=None if config.follow_up is None else PostgresCycleQueue(database),
        follow_up_policy=config.follow_up_policy(),
        follow_up_limits=config.follow_up_limits(),
        impact_policy=config.impact_policy(),
        post_mortems=PostgresPostMortemStore(database),
        post_mortem_policy=config.post_mortem_policy(),
        triage_queue=None if config.noise is None else PostgresTriageQueue(database),
        triage_policy=config.triage_policy(),
        console=None if config.console is None else PostgresConsoleStore(database),
        console_policy=config.console_policy(),
    )


def _embedder(settings: WorkerSettings, config: TeamConfig) -> TextEmbedder | None:
    """O modelo de embeddings da memória com RAG; ausente na imagem, o worker não sobe."""
    if config.memory.rag is None:
        return None
    try:
        return FastembedTextEmbedder(config.memory.rag.size)
    except EmbeddingError as error:
        raise ConfigError(f"{settings.config_file}: memory.rag.size: {error}") from error


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
