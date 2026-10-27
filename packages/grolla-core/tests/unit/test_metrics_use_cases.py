"""Casos de uso das métricas do agente: responder o "já vi isso", avaliar e agregar."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.metrics import agent_metrics, answer_seen_before, evaluate_agent
from grolla.application.post_mortem import PostMortemNotFoundError
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.analysis import SeenBefore
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.metrics import Granularity
from grolla.domain.post_mortem import AgentEvaluation, InvalidPostMortemError, PostMortemStatus
from grolla.domain.war_room import InvalidActionError
from grolla.testing.builders import T0, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.metrics import InMemoryMetricsReader
from grolla.testing.post_mortem import InMemoryPostMortemStore, make_post_mortem

NOW = datetime(2026, 10, 27, 15, tzinfo=UTC)
PAST = uuid4()


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.store = InMemoryPostMortemStore(self.repository)
        self.reader = InMemoryMetricsReader(self.repository, self.store)
        self.clock = FakeClock(NOW)

    async def incident(self, *, seen_before: bool = True) -> UUID:
        incident = make_incident()
        analysis = make_analysis(incident.id)
        if seen_before:
            analysis = replace(analysis, seen_before=SeenBefore(PAST, "Pool esgotado", T0, "igual"))
        async with self.repository.transaction() as uow:
            await uow.save(incident)
        self.repository.analyses[incident.id] = analysis
        return incident.id

    async def answer(self, incident_id: UUID, *, same: bool, author: str = "Ana") -> None:
        await answer_seen_before(
            repository=self.repository,
            incident_id=incident_id,
            same=same,
            author=author,
            clock=self.clock,
        )


async def test_answer_becomes_an_event_with_the_past_incident_and_the_author() -> None:
    world = World()
    incident_id = await world.incident()

    await world.answer(incident_id, same=True, author=" Ana ")

    assert world.repository.events == [
        IncidentEvent(
            incident_id,
            NOW,
            EventType.SEEN_BEFORE_ANSWERED,
            {"past": str(PAST), "same": True},
            "Ana",
        )
    ]


async def test_answer_is_refused_without_author_or_seen_before() -> None:
    world = World()
    incident_id = await world.incident()
    plain = await world.incident(seen_before=False)
    no_analysis = make_incident()
    async with world.repository.transaction() as uow:
        await uow.save(no_analysis)

    with pytest.raises(InvalidActionError, match="informe o seu nome"):
        await world.answer(incident_id, same=True, author=" ")
    for target in (plain, no_analysis.id):
        with pytest.raises(InvalidActionError, match="não apontou um caso parecido"):
            await world.answer(target, same=False)
    with pytest.raises(IncidentNotFoundError):
        await world.answer(uuid4(), same=True)
    assert world.repository.events == []


async def test_evaluation_is_saved_with_the_author() -> None:
    world = World()
    incident_id = await world.incident()
    await world.store.save(make_post_mortem(incident_id, status=PostMortemStatus.APPROVED), [])
    evaluation = AgentEvaluation(main_hypothesis_correct=True, minutes_to_right=8)

    pm = await evaluate_agent(
        store=world.store,
        incident_id=incident_id,
        evaluation=evaluation,
        author="Ana",
        clock=world.clock,
    )

    assert pm.evaluation == replace(evaluation, author="Ana", at=NOW)
    assert await world.store.get(incident_id) == pm


async def test_evaluation_is_refused_for_unknown_or_closed_post_mortem() -> None:
    world = World()
    incident_id = await world.incident()
    await world.store.save(make_post_mortem(incident_id, status=PostMortemStatus.CLOSED), [])

    async def evaluate(target: UUID) -> None:
        await evaluate_agent(
            store=world.store,
            incident_id=target,
            evaluation=AgentEvaluation(),
            author="Ana",
            clock=world.clock,
        )

    with pytest.raises(PostMortemNotFoundError):
        await evaluate(uuid4())
    with pytest.raises(InvalidPostMortemError, match="encerrado"):
        await evaluate(incident_id)


async def test_metrics_cover_the_periods_from_the_oldest_one() -> None:
    world = World()
    confirmed = await world.incident()
    await world.answer(confirmed, same=True)
    approved = await world.incident()
    evaluation = AgentEvaluation(main_hypothesis_correct=True, minutes_to_right=8)
    pm = replace(
        make_post_mortem(approved, status=PostMortemStatus.APPROVED),
        status_at=NOW - timedelta(days=33),
        evaluation=evaluation,
    )
    await world.store.save(pm, [])
    too_old = replace(
        make_incident(state=IncidentState.RESOLVED), resolved_at=NOW - timedelta(days=90)
    )
    async with world.repository.transaction() as uow:
        await uow.save(too_old)

    months = await agent_metrics(world.reader, world.clock, Granularity.MONTH, count=2)
    weeks = await agent_metrics(world.reader, world.clock, Granularity.WEEK)

    assert [m.period.label for m in months] == ["out/2026", "set/2026"]
    assert (months[0].seen_before_confirmed, months[1].correct) == (1, 1)
    assert months[1].median_minutes == 8.0
    assert sum(m.resolved for m in months) == 0
    assert len(weeks) == 6
    assert weeks[0].period.label == "semana de 26/10"
    assert sum(w.approved for w in weeks) == 1  # 24/09 cai na semana mais antiga, a de 21/09
