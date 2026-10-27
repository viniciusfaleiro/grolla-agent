"""Suíte de contrato de MetricsReader.

O adapter sobrescreve `repository`, `post_mortems` e `reader`, que precisam enxergar o mesmo
armazenamento. Cada teste começa com o armazenamento vazio (no banco real, um banco próprio do
módulo com as tabelas esvaziadas antes de cada teste). A ordem das tuplas dos fatos é livre: a
suíte compara ordenado.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from grolla.application.incidents import IncidentRepository
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import PostMortemStore
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.metrics import MetricsFacts
from grolla.domain.post_mortem import AgentEvaluation, PostMortemStatus
from grolla.domain.safety import Scope
from grolla.testing.builders import T0, make_incident
from grolla.testing.post_mortem import make_post_mortem

SINCE = T0 + timedelta(days=1)
EVALUATION = AgentEvaluation(
    main_hypothesis_correct=True,
    minutes_to_right=8,
    memory_helped=False,
    author="Ana Souza",
    at=SINCE + timedelta(hours=2),
)


async def incident(
    repository: IncidentRepository,
    *,
    state: IncidentState = IncidentState.RESOLVED,
    resolved_at: datetime | None = None,
) -> UUID:
    saved = replace(make_incident(state=state), resolved_at=resolved_at)
    async with repository.transaction() as uow:
        await uow.save(saved)
    return saved.id


async def answer(
    repository: IncidentRepository, incident_id: UUID, at: datetime, *, same: bool
) -> None:
    data: dict[str, Any] = {"past": "00000000-0000-0000-0000-000000000012", "same": same}
    event = IncidentEvent(incident_id, at, EventType.SEEN_BEFORE_ANSWERED, data, "Ana Souza")
    async with repository.transaction() as uow:
        await uow.append([event])


def ordered(facts: MetricsFacts) -> MetricsFacts:
    return MetricsFacts(
        resolved=tuple(sorted(facts.resolved)),
        approved=tuple(sorted(facts.approved, key=lambda a: a[0])),
        answers=tuple(sorted(facts.answers)),
        refusals=tuple(sorted(facts.refusals)),
    )


class MetricsReaderContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def post_mortems(self, repository: IncidentRepository) -> PostMortemStore:
        raise NotImplementedError

    @pytest.fixture
    def reader(
        self, repository: IncidentRepository, post_mortems: PostMortemStore
    ) -> MetricsReader:
        raise NotImplementedError

    async def test_empty_storage_has_no_facts(self, reader: MetricsReader) -> None:
        assert await reader.facts(SINCE) == MetricsFacts()

    async def test_resolved_are_the_resolved_incidents_since(
        self, repository: IncidentRepository, reader: MetricsReader
    ) -> None:
        later = SINCE + timedelta(days=3)
        await incident(repository, resolved_at=later)
        await incident(repository, resolved_at=SINCE)
        await incident(repository, resolved_at=SINCE - timedelta(seconds=1))
        await incident(repository, state=IncidentState.DISCARDED, resolved_at=later)
        await incident(repository, state=IncidentState.HANDLING)

        facts = ordered(await reader.facts(SINCE))

        assert facts.resolved == (SINCE, later)

    async def test_approved_are_approved_or_closed_post_mortems_with_the_evaluation(
        self,
        repository: IncidentRepository,
        post_mortems: PostMortemStore,
        reader: MetricsReader,
    ) -> None:
        approved_at = SINCE + timedelta(hours=1)
        closed_at = SINCE + timedelta(days=2)
        statuses = (
            (PostMortemStatus.APPROVED, approved_at, EVALUATION),
            (PostMortemStatus.CLOSED, closed_at, AgentEvaluation()),
            (PostMortemStatus.REVIEW, approved_at, EVALUATION),
            (PostMortemStatus.APPROVED, SINCE - timedelta(seconds=1), EVALUATION),
        )
        for status, at, evaluation in statuses:
            incident_id = await incident(repository, resolved_at=T0)
            pm = replace(
                make_post_mortem(incident_id, status=status),
                status_at=at,
                evaluation=evaluation,
            )
            await post_mortems.save(pm, [])

        facts = ordered(await reader.facts(SINCE))

        assert facts.approved == ((approved_at, EVALUATION), (closed_at, AgentEvaluation()))

    async def test_answers_are_the_last_answer_of_each_incident_since(
        self, repository: IncidentRepository, reader: MetricsReader
    ) -> None:
        first = await incident(repository, state=IncidentState.HANDLING)
        second = await incident(repository, state=IncidentState.HANDLING)
        old = await incident(repository, state=IncidentState.HANDLING)
        await answer(repository, first, SINCE + timedelta(minutes=1), same=True)
        await answer(repository, first, SINCE + timedelta(minutes=5), same=False)
        await answer(repository, second, SINCE + timedelta(minutes=3), same=True)
        await answer(repository, old, SINCE - timedelta(minutes=1), same=True)
        async with repository.transaction() as uow:
            other = IncidentEvent(second, SINCE, EventType.HUMAN_MESSAGE, {"text": "oi"}, "Ana")
            await uow.append([other])

        facts = ordered(await reader.facts(SINCE))

        assert facts.answers == (
            (SINCE + timedelta(minutes=3), True),
            (SINCE + timedelta(minutes=5), False),
        )

    async def test_refusals_are_the_refused_replies_since(
        self, repository: IncidentRepository, reader: MetricsReader
    ) -> None:
        incident_id = await incident(repository, state=IncidentState.HANDLING)
        refused_at = SINCE + timedelta(minutes=2)
        replies = (
            (refused_at, Scope.OFF_TOPIC),
            (SINCE + timedelta(minutes=4), None),
            (SINCE - timedelta(minutes=1), Scope.EXECUTION),
        )
        async with repository.transaction() as uow:
            for at, refusal in replies:
                reply = ChatMessage(0, incident_id, AGENT_AUTHOR, "-", at, refusal=refusal)
                await uow.add_message(reply, wants_reply=False)

        facts = ordered(await reader.facts(SINCE))

        assert facts.refusals == (refused_at,)
