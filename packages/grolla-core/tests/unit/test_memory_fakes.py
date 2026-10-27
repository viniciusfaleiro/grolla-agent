"""Os fakes da memória viva passam nas suítes de contrato."""

import pytest
from contracts.learning_queue_contract import LearningQueueContract
from contracts.memory_store_contract import MemoryChunksContract, MemoryStoreContract
from contracts.study_queue_contract import StudyQueueContract
from contracts.text_embedder_contract import TextEmbedderContract
from grolla.application.incidents import IncidentRepository
from grolla.application.memory import LearningQueue, StudyQueue
from grolla.application.memory_store import EmbeddingError, MemoryStore, TextEmbedder
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.memory import (
    FakeTextEmbedder,
    InMemoryLearningQueue,
    InMemoryMemoryStore,
    InMemoryStudyQueue,
)


class TestInMemoryMemoryStoreContract(MemoryStoreContract):
    @pytest.fixture
    def memory(self) -> MemoryStore:
        return InMemoryMemoryStore()


class TestInMemoryMemoryChunksContract(MemoryChunksContract):
    @pytest.fixture
    def memory(self) -> MemoryStore:
        return InMemoryMemoryStore()


class TestFakeTextEmbedderContract(TextEmbedderContract):
    @pytest.fixture
    def embedder(self) -> TextEmbedder:
        return FakeTextEmbedder({"pool": "conexao"})


async def test_fake_embedder_maps_concepts_to_the_same_vector() -> None:
    embedder = FakeTextEmbedder({"timeout": "esgotamento"})

    timeout, exhaustion, empty = await embedder.embed(["Timeout", "esgotamento", "!"], "query")

    assert timeout == exhaustion
    assert sum(v * v for v in empty) == 1
    assert embedder.calls == [(("Timeout", "esgotamento", "!"), "query")]


async def test_failing_fake_embedder_raises_embedding_error() -> None:
    with pytest.raises(EmbeddingError):
        await FakeTextEmbedder(failing=True).embed(["texto"], "passage")


class TestInMemoryLearningQueueContract(LearningQueueContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> LearningQueue:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryLearningQueue(repository)


class TestInMemoryStudyQueueContract(StudyQueueContract):
    @pytest.fixture
    def studies(self) -> StudyQueue:
        return InMemoryStudyQueue()
