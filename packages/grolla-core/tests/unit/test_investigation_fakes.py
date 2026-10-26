"""Os fakes da investigação e da fila de análises passam nas suítes de contrato."""

import pytest
from contracts.analysis_queue_contract import AnalysisQueueContract, VerificationQueueContract
from contracts.change_history_contract import ChangeHistoryContract
from contracts.chat_queue_contract import ChatQueueContract, WarRoomRepositoryContract
from contracts.cloud_inventory_contract import CloudInventoryContract
from contracts.language_model_contract import ChatReplyContract, LanguageModelContract
from contracts.telemetry_contract import TelemetryContract
from grolla.application.analysis import AnalysisQueue
from grolla.application.chat import ChatQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import (
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
