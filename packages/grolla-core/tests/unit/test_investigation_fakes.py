"""Os fakes da investigação e da fila de análises passam nas suítes de contrato."""

import pytest
from contracts.analysis_queue_contract import AnalysisQueueContract
from contracts.change_history_contract import ChangeHistoryContract
from contracts.language_model_contract import LanguageModelContract
from contracts.telemetry_contract import TelemetryContract
from grolla.application.analysis import AnalysisQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import (
    ChangeHistory,
    InvalidAnalysisError,
    LanguageModel,
    LanguageModelError,
    Telemetry,
)
from grolla.testing.incidents import (
    InMemoryAnalysisQueue,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)
from grolla.testing.investigation import (
    FakeChangeHistory,
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
