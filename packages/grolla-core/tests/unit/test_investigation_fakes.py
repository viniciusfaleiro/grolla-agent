"""Os fakes da investigação e da fila de análises passam nas suítes de contrato."""

import pytest
from contracts.analysis_queue_contract import AnalysisQueueContract, VerificationQueueContract
from contracts.change_history_contract import (
    ChangeHistoryContract,
    RepositoryDocumentsContract,
    RepositoryFilesContract,
)
from contracts.chat_queue_contract import ChatQueueContract, WarRoomRepositoryContract
from contracts.cloud_inventory_contract import (
    CloudHealthQuotasContract,
    CloudInventoryContract,
    CloudOverviewContract,
)
from contracts.cluster_inventory_contract import ClusterInventoryContract
from contracts.cycle_queue_contract import CycleQueueContract
from contracts.language_model_contract import (
    SERVICE,
    ChatReplyContract,
    FollowUpModelContract,
    LanguageModelContract,
    MemoryModelContract,
)
from contracts.telemetry_contract import EnvironmentTelemetryContract, TelemetryContract
from grolla.application.analysis import AnalysisQueue
from grolla.application.chat import ChatQueue
from grolla.application.follow_up import CycleQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import (
    HEALTH_UNAVAILABLE,
    NO_CLUSTER_CHANGES,
    READ_LIMIT,
    AlertRule,
    ChangeHistory,
    CloudInventory,
    ClusterInventory,
    InvalidAnalysisError,
    LanguageModel,
    LanguageModelError,
    Telemetry,
)
from grolla.testing.builders import T0
from grolla.testing.follow_up import InMemoryCycleQueue
from grolla.testing.incidents import (
    InMemoryAnalysisQueue,
    InMemoryChatQueue,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)
from grolla.testing.investigation import (
    CLOUD_TOOLS,
    FakeChangeHistory,
    FakeCloudInventory,
    FakeClusterInventory,
    FakeTelemetry,
    ScriptedLanguageModel,
)

TOOLS = [
    ("query_metric", {"service": "authorizer-api", "signal": "taxa_de_erro"}),
    ("recent_deploys", {"service": "authorizer-api"}),
]


class TestFakeTelemetryContract(TelemetryContract):
    @pytest.fixture
    def telemetry(self) -> Telemetry:
        return FakeTelemetry()

    @pytest.fixture
    def unavailable_telemetry(self) -> Telemetry:
        return FakeTelemetry(failing={"query_metric", "search_logs", "find_traces"})


class TestFakeChangeHistoryContract(ChangeHistoryContract):
    @pytest.fixture
    def changes(self, service: str) -> ChangeHistory:
        return FakeChangeHistory({service: "1 deploy: 2.4.1 às 09:58 por ana"})

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        return FakeChangeHistory(unavailable=True)


class TestFakeCloudInventoryContract(CloudInventoryContract):
    @pytest.fixture
    def cloud(self, service: str) -> CloudInventory:
        return FakeCloudInventory(
            resources={service: "1 recurso: banco pedidos-db (stopped)"},
            changes={service: "21:42 parada do banco pedidos-db por ana"},
        )

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        return FakeCloudInventory(failing=CLOUD_TOOLS)


class TestFakeCloudHealthQuotasContract(CloudHealthQuotasContract):
    @pytest.fixture
    def cloud(self, service: str) -> CloudInventory:
        return FakeCloudInventory(
            health={service: "evento aberto: banco de dados em sa-east-1 (degradado)"},
            quotas={service: "instâncias de banco: 18 de 20 (90%)"},
        )

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        return FakeCloudInventory(failing=CLOUD_TOOLS)

    @pytest.fixture
    def cloud_without_health_plan(self, service: str) -> CloudInventory:
        return FakeCloudInventory(health={service: HEALTH_UNAVAILABLE})


async def test_fake_cloud_answers_no_events_and_no_quota_near_the_limit_by_default() -> None:
    cloud = FakeCloudInventory()

    health = await cloud.health("pedidos-api")
    quotas = await cloud.quotas("pedidos-api")

    assert health.summary == "nenhum evento aberto do provedor para pedidos-api"
    assert quotas.summary == "nenhuma cota perto do limite"
    assert cloud.calls == [("health", "pedidos-api"), ("quotas", "pedidos-api")]


class TestFakeClusterInventoryContract(ClusterInventoryContract):
    @pytest.fixture
    def cluster(self, service: str) -> ClusterInventory:
        return FakeClusterInventory(
            state={service: "deployment pedidos-api: 2 de 3 prontas; pod pedidos-api-1 OOMKilled"},
            changes={service: "03:10 ConfigMap pedidos-config alterado por kubectl-edit"},
        )

    @pytest.fixture
    def unavailable_cluster(self) -> ClusterInventory:
        return FakeClusterInventory(failing={"state", "changes"})


async def test_fake_cluster_records_the_calls_and_answers_no_changes_by_default() -> None:
    cluster = FakeClusterInventory()

    changes = await cluster.changes("pedidos-api", T0)

    assert changes.summary == NO_CLUSTER_CHANGES
    assert cluster.calls == [("changes", "pedidos-api")]
    assert cluster.since == [T0]


class TestScriptedLanguageModelContract(LanguageModelContract):
    @pytest.fixture
    def model(self) -> LanguageModel:
        return ScriptedLanguageModel(calls=TOOLS)

    @pytest.fixture
    def failing_model(self) -> LanguageModel:
        return ScriptedLanguageModel(error=LanguageModelError("API fora do ar"))

    @pytest.fixture
    def invalid_model(self) -> LanguageModel:
        return ScriptedLanguageModel(calls=TOOLS, error=InvalidAnalysisError("fora do schema"))

    @pytest.fixture
    def verifying_model(self) -> LanguageModel:
        return ScriptedLanguageModel(verification_calls=TOOLS)

    @pytest.fixture
    def failing_verifier(self) -> LanguageModel:
        return ScriptedLanguageModel(verification_error=LanguageModelError("API fora do ar"))

    @pytest.fixture
    def invalid_verifier(self) -> LanguageModel:
        return ScriptedLanguageModel(
            verification_calls=TOOLS, verification_error=InvalidAnalysisError("fora do schema")
        )


class TestScriptedLanguageModelChatContract(ChatReplyContract):
    @pytest.fixture
    def replying_model(self) -> LanguageModel:
        return ScriptedLanguageModel(chat_calls=TOOLS)

    @pytest.fixture
    def failing_replier(self) -> LanguageModel:
        return ScriptedLanguageModel(chat_error=LanguageModelError("API fora do ar"))


class TestScriptedLanguageModelFollowUpContract(FollowUpModelContract):
    @pytest.fixture
    def following_model(self) -> LanguageModel:
        return ScriptedLanguageModel(follow_up_calls=TOOLS)

    @pytest.fixture
    def failing_follower(self) -> LanguageModel:
        return ScriptedLanguageModel(follow_up_error=LanguageModelError("API fora do ar"))


class TestInMemoryCycleQueueContract(CycleQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> CycleQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryCycleQueue(repository)

    @pytest.fixture
    def analysis_queue(self, repository: IncidentRepository) -> AnalysisQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryAnalysisQueue(repository)

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryNotificationOutbox(repository)


class TestInMemoryWarRoomRepositoryContract(WarRoomRepositoryContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()


class TestInMemoryChatQueueContract(ChatQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> ChatQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryChatQueue(repository)


class TestInMemoryAnalysisQueueContract(AnalysisQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> AnalysisQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryAnalysisQueue(repository)

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryNotificationOutbox(repository)


class TestInMemoryVerificationQueueContract(VerificationQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> AnalysisQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryAnalysisQueue(repository)

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryNotificationOutbox(repository)


class TestFakeEnvironmentTelemetryContract(EnvironmentTelemetryContract):
    @pytest.fixture
    def telemetry(self, service: str) -> Telemetry:
        rule = AlertRule("Taxa de erro alta", "erro > 5%", "critical", {}, service, None)
        return FakeTelemetry(
            rules=[rule],
            services=[service],
            dependencies={service: "banco grolla-lab-db: 1200 chamadas, 0% de erro, p99 12 ms"},
        )

    @pytest.fixture
    def unavailable_telemetry(self) -> Telemetry:
        return FakeTelemetry(failing={"alert_rules", "active_services", "dependencies"})


class TestFakeRepositoryFilesContract(RepositoryFilesContract):
    @pytest.fixture
    def changes(self, service: str, large_path: str, binary_path: str) -> ChangeHistory:
        files = {
            "README.md": "# authorizer-api\nAutoriza transações.",
            large_path: "x" * (READ_LIMIT + 10),
            binary_path: "\x89PNG\x00\x00",
        }
        return FakeChangeHistory(files={service: files})

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        return FakeChangeHistory(unavailable=True)


class TestFakeRepositoryDocumentsContract(RepositoryDocumentsContract):
    @pytest.fixture
    def changes(self, service: str) -> ChangeHistory:
        files = {
            "README.md": "# authorizer-api\nAutoriza transações.",
            "docs/runbook.md": "## Pool de conexões esgotado\nReinicie as conexões presas.",
            "docs/Arquitetura.MD": "# Arquitetura\nUm serviço só, com banco próprio e fila.",
            "src/app.py": "print('oi')",
        }
        return FakeChangeHistory(files={service: files})

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        return FakeChangeHistory(unavailable=True)


class TestFakeCloudOverviewContract(CloudOverviewContract):
    @pytest.fixture
    def cloud(self) -> CloudInventory:
        return FakeCloudInventory()

    @pytest.fixture
    def unavailable_cloud(self) -> CloudInventory:
        return FakeCloudInventory(failing=CLOUD_TOOLS)


class TestScriptedLanguageModelMemoryContract(MemoryModelContract):
    @pytest.fixture
    def memory_model(self) -> LanguageModel:
        return ScriptedLanguageModel(study_calls=TOOLS)

    @pytest.fixture
    def failing_memory_model(self) -> LanguageModel:
        error = LanguageModelError("API fora do ar")
        return ScriptedLanguageModel(
            learning_error=error, study_errors={SERVICE: error}, synthesis_error=error
        )
