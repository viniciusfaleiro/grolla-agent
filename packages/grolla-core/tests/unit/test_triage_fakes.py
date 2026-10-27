"""Os fakes da triagem passam nas suítes de contrato."""

import pytest
from contracts.language_model_contract import TriageModelContract
from contracts.triage_queue_contract import TriageQueueContract
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import STAGE_TRIAGE, LanguageModel, LanguageModelError
from grolla.application.triage import TriageQueue
from grolla.testing.builders import T0, make_incident
from grolla.testing.incidents import InMemoryIncidentRepository, InMemoryNotificationOutbox
from grolla.testing.investigation import ScriptedLanguageModel, fake_usage
from grolla.testing.triage import InMemoryTriageQueue


class TestInMemoryTriageQueueContract(TriageQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> TriageQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryTriageQueue(repository)

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryNotificationOutbox(repository)


class TestScriptedLanguageModelTriageContract(TriageModelContract):
    @pytest.fixture
    def triage_model(self) -> LanguageModel:
        return ScriptedLanguageModel()

    @pytest.fixture
    def failing_triage_model(self) -> LanguageModel:
        return ScriptedLanguageModel(triage_error=LanguageModelError("API fora do ar"))


async def test_finish_records_the_model_usage_on_the_incident() -> None:
    repository = InMemoryIncidentRepository()
    queue = InMemoryTriageQueue(repository)
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.request_triage(incident.id, T0)
    job = await queue.claim(T0)
    assert job is not None
    usage = fake_usage(500, STAGE_TRIAGE)

    await queue.finish(
        job, None, events=[], notifications=[], usages=[usage], reason="falhou", at=T0
    )

    assert repository.usages == [(incident.id, usage, T0)]
    assert queue.jobs[incident.id].reason == "falhou"
