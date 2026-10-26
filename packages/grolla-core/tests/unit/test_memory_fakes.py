"""Os fakes da memória viva passam nas suítes de contrato."""

import pytest
from contracts.learning_queue_contract import LearningQueueContract
from contracts.memory_store_contract import MemoryStoreContract
from contracts.study_queue_contract import StudyQueueContract
from grolla.application.incidents import IncidentRepository
from grolla.application.memory import LearningQueue, StudyQueue
from grolla.application.memory_store import MemoryStore
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.memory import InMemoryLearningQueue, InMemoryMemoryStore, InMemoryStudyQueue


class TestInMemoryMemoryStoreContract(MemoryStoreContract):
    @pytest.fixture
    def memory(self) -> MemoryStore:
        return InMemoryMemoryStore()


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
