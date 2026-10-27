"""Os fakes da investigação e da fila de análises passam nas suítes de contrato."""

import pytest
from contracts.analysis_queue_contract import AnalysisQueueContract, VerificationQueueContract
from contracts.change_history_contract import (
    ChangeHistoryContract,
    RepositoryDocumentsContract,
    RepositoryFilesContract,
)
from contracts.chat_queue_contract import ChatQueueContract, WarRoomRepositoryContract
from contracts.cloud_inventory_contract import CloudInventoryContract, CloudOverviewContract
from contracts.language_model_contract import (
    SERVICE,
    ChatReplyContract,
    LanguageModelContract,
    MemoryModelContract,
)
from contracts.telemetry_contract import EnvironmentTelemetryContract, TelemetryContract
from grolla.application.analysis import AnalysisQueue
from grolla.application.chat import ChatQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import (
    READ_LIMIT,
    AlertRule,
    ChangeHistory,
    CloudInventory,
    InvalidAnalysisError,
    LanguageModel,
    LanguageModelError,
    Telemetry,
)
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
