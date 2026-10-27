"""O fake das métricas do agente passa na suíte de contrato."""

from datetime import timedelta
from uuid import uuid4

import pytest
from contracts.metrics_reader_contract import MetricsReaderContract
from grolla.application.incidents import IncidentRepository
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import PostMortemStore
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.console import ConsoleMessage, ConsoleSession
from grolla.domain.safety import Scope
from grolla.testing.builders import T0
from grolla.testing.console import InMemoryConsoleStore
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


async def test_refusals_of_the_console_count_when_the_reader_sees_it() -> None:
    repository = InMemoryIncidentRepository()
    console = InMemoryConsoleStore()
    session = ConsoleSession(uuid4(), "poema", "Ana", T0, T0)
    await console.create_session(session, ConsoleMessage(0, session.id, "Ana", "poema?", T0))
    job = await console.claim(T0)
    assert job is not None
    later = T0 + timedelta(minutes=1)
    reply = ConsoleMessage(0, session.id, AGENT_AUTHOR, "-", later, refusal=Scope.OFF_TOPIC)
    await console.answer(job, reply, [], later)
    post_mortems = InMemoryPostMortemStore(repository)

    with_console = await InMemoryMetricsReader(repository, post_mortems, console).facts(T0)
    without = await InMemoryMetricsReader(repository, post_mortems).facts(T0)

    assert (with_console.refusals, without.refusals) == ((later,), ())
