"""O fake das métricas do agente passa na suíte de contrato."""

import pytest
from contracts.metrics_reader_contract import MetricsReaderContract
from grolla.application.incidents import IncidentRepository
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import PostMortemStore
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.metrics import InMemoryMetricsReader
from grolla.testing.post_mortem import InMemoryPostMortemStore


class TestInMemoryMetricsReaderContract(MetricsReaderContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def post_mortems(self, repository: IncidentRepository) -> PostMortemStore:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryPostMortemStore(repository)

    @pytest.fixture
    def reader(
        self, repository: IncidentRepository, post_mortems: PostMortemStore
    ) -> MetricsReader:
        assert isinstance(repository, InMemoryIncidentRepository)
        assert isinstance(post_mortems, InMemoryPostMortemStore)
        return InMemoryMetricsReader(repository, post_mortems)
