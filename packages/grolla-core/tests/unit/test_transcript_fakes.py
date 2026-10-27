"""Os fakes da transcrição de call passam nas suítes de contrato."""

import pytest
from contracts.language_model_contract import TranscriptModelContract
from contracts.transcript_store_contract import TranscriptStoreContract, drain, uploaded
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_TRANSCRIPT, LanguageModel, LanguageModelError
from grolla.application.transcript import TranscriptStore
from grolla.testing.builders import T0
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import ScriptedLanguageModel, fake_usage
from grolla.testing.transcript import InMemoryTranscriptStore


class TestInMemoryTranscriptStoreContract(TranscriptStoreContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def store(self, repository: IncidentRepository) -> TranscriptStore:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryTranscriptStore(repository)


class TestScriptedLanguageModelTranscriptContract(TranscriptModelContract):
    @pytest.fixture
    def transcript_model(self) -> LanguageModel:
        return ScriptedLanguageModel()

    @pytest.fixture
    def failing_transcript_model(self) -> LanguageModel:
        return ScriptedLanguageModel(transcript_error=LanguageModelError("API fora do ar"))


async def test_finish_records_the_model_usage_on_the_incident() -> None:
    repository = InMemoryIncidentRepository()
    store = InMemoryTranscriptStore(repository)
    saved = await uploaded(repository, store)
    (job,) = await drain(store)
    usage = fake_usage(500, STAGE_TRANSCRIPT)

    await store.finish(job, [], [usage], None, T0)

    assert repository.usages == [(saved.incident_id, usage, T0)]
