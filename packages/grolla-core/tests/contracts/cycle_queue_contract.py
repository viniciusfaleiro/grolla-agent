"""Suíte de contrato do acompanhamento da crise: `CycleQueue`, o `request_cycle` do unit of work
e o `cycles_of` do repositório.

O adapter sobrescreve `repository`, `queue`, `analysis_queue` e `outbox`, que precisam enxergar
o mesmo armazenamento (a análise inicial é encerrada pela fila de análises, como no worker). A
suíte roda também contra o banco real compartilhado: cada teste cria incidentes novos (uuid4),
esvazia a fila com `drain` e só confere os próprios registros.

Regras:
- `candidates` lista os incidentes abertos com as severidades pedidas; `analysis_done_at` é o
  fim da análise inicial concluída ou parcial (None se ainda pendente ou em andamento) e
  `tokens` soma só o uso da etapa `follow_up`;
- `request_cycle` repetido fica com o pedido mais recente;
- `finish` só grava se o ciclo ainda está em andamento, e zera o pedido antecipado.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.analysis import AnalysisJob, AnalysisQueue
from grolla.application.follow_up import CycleCandidate, CycleQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import STAGE_FOLLOW_UP, STAGE_INVESTIGATION
from grolla.domain.analysis import AnalysisStatus, Evidence, PartialReason
from grolla.domain.follow_up import Cycle, CycleStatus, Trend
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import (
    T0,
    make_analysis,
    make_evaluations,
    make_incident,
    make_notification,
)
from grolla.testing.investigation import fake_usage

URGENT = (Severity.P0, Severity.P1)
STARTED = T0 + timedelta(minutes=6)
FINISHED = T0 + timedelta(minutes=7)


async def saved(
    repository: IncidentRepository,
    severity: Severity = Severity.P1,
    state: IncidentState = IncidentState.HANDLING,
) -> Incident:
    incident = make_incident(severity=severity, state=state)
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident


async def drain(queue: CycleQueue, now: datetime = STARTED) -> list[Cycle]:
    """Reivindica tudo que está pendente, na ordem em que a fila entrega."""
    cycles: list[Cycle] = []
    while (cycle := await queue.claim(now)) is not None:
        cycles.append(cycle)
    return cycles


async def candidate(queue: CycleQueue, incident_id: UUID) -> CycleCandidate | None:
    return next((c for c in await queue.candidates(URGENT) if c.incident.id == incident_id), None)


async def running(repository: IncidentRepository, queue: CycleQueue) -> Cycle:
    """Incidente novo com o ciclo 1 em andamento desde STARTED."""
    incident = await saved(repository)
    await queue.enqueue(incident.id, 1, T0 + timedelta(minutes=5))
    return next(c for c in await drain(queue) if c.incident_id == incident.id)


def done(cycle: Cycle) -> Cycle:
    return replace(
        cycle,
        status=CycleStatus.COMPLETED,
        finished_at=FINISHED,
        trend=Trend.BETTER,
        error_rate=12.5,
        summary="Erro caiu de 30% para 12,5%.",
        changed=True,
        evidence=(Evidence("taxa de erro em 12,5%", "https://origem.interno/metrica"),),
        evaluations=make_evaluations(),
        posted=True,
    )


class CycleQueueContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> CycleQueue:
        raise NotImplementedError

    @pytest.fixture
    def analysis_queue(self, repository: IncidentRepository) -> AnalysisQueue:
        raise NotImplementedError

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        raise NotImplementedError

    async def test_candidates_are_the_open_incidents_of_the_severities(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        handling = await saved(repository)
        p0 = await saved(repository, severity=Severity.P0, state=IncidentState.MITIGATED)
        p3 = await saved(repository, severity=Severity.P3, state=IncidentState.INFORMED)
        resolved = await saved(repository, state=IncidentState.RESOLVED)
        discarded = await saved(repository, state=IncidentState.DISCARDED)

        found = {c.incident.id: c for c in await queue.candidates(URGENT)}

        assert found[handling.id] == CycleCandidate(handling, None, (), None, 0)
        assert found[p0.id].incident == p0
        assert {p3.id, resolved.id, discarded.id}.isdisjoint(found)
        only_p3 = {c.incident.id for c in await queue.candidates([Severity.P3])}
        assert p3.id in only_p3
        assert handling.id not in only_p3

    async def test_candidate_knows_when_the_initial_analysis_ended(
        self,
        repository: IncidentRepository,
        queue: CycleQueue,
        analysis_queue: AnalysisQueue,
    ) -> None:
        finished, partial, pending = [await saved(repository) for _ in range(3)]
        async with repository.transaction() as uow:
            for incident in (finished, partial, pending):
                await uow.request_analysis(incident.id, T0)
        claimed = {j.incident_id for j in await _drain_analyses(analysis_queue)}
        assert {finished.id, partial.id} <= claimed
        ended = T0 + timedelta(minutes=2)
        for incident_id, status in (
            (finished.id, AnalysisStatus.COMPLETED),
            (partial.id, AnalysisStatus.PARTIAL),
        ):
            analysis = make_analysis(incident_id, status=status, finished_at=ended)
            card = make_notification(incident_id, NotificationKind.ANALYSIS_READY)
            await analysis_queue.finish(analysis, [], card, [fake_usage()], ended)

        for incident_id in (finished.id, partial.id):
            found = await candidate(queue, incident_id)
            assert found is not None
            assert found.analysis_done_at == ended
            assert found.tokens == 0  # o uso da análise não é do acompanhamento
        still = await candidate(queue, pending.id)
        assert still is not None
        assert still.analysis_done_at is None

    async def test_request_cycle_marks_the_candidate_with_the_latest_request(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        incident = await saved(repository)
        later = T0 + timedelta(minutes=3)

        async with repository.transaction() as uow:
            await uow.request_cycle(incident.id, T0 + timedelta(minutes=2))
        async with repository.transaction() as uow:
            await uow.request_cycle(incident.id, later)

        found = await candidate(queue, incident.id)
        assert found is not None
        assert found.requested_at == later

    async def test_enqueue_creates_the_pending_cycle_once(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        incident = await saved(repository)
        at = T0 + timedelta(minutes=5)

        assert await queue.enqueue(incident.id, 1, at)
        assert not await queue.enqueue(incident.id, 1, at + timedelta(seconds=30))

        pending = Cycle(incident.id, 1, CycleStatus.PENDING, at)
        assert await repository.cycles_of(incident.id) == [pending]
        found = await candidate(queue, incident.id)
        assert found is not None
        assert found.cycles == (pending,)

    async def test_claim_takes_the_oldest_pending_cycle_and_marks_it_running(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        newer, older = await saved(repository), await saved(repository)
        await queue.enqueue(newer.id, 1, T0 + timedelta(seconds=2))
        await queue.enqueue(older.id, 1, T0 + timedelta(seconds=1))

        claimed = [c for c in await drain(queue) if c.incident_id in {newer.id, older.id}]

        assert [c.incident_id for c in claimed] == [older.id, newer.id]
        assert claimed[0] == Cycle(
            older.id, 1, CycleStatus.RUNNING, T0 + timedelta(seconds=1), started_at=STARTED
        )
        assert await repository.cycles_of(older.id) == [claimed[0]]
        assert all(c.incident_id not in {newer.id, older.id} for c in await drain(queue))

    async def test_stuck_lists_cycles_running_since_before_the_limit(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        cycle = await running(repository, queue)

        assert cycle in await queue.stuck(STARTED + timedelta(minutes=1))
        assert cycle not in await queue.stuck(STARTED)

    async def test_finish_writes_cycle_events_cards_and_usage_and_clears_the_request(
        self,
        repository: IncidentRepository,
        queue: CycleQueue,
        outbox: NotificationOutbox,
    ) -> None:
        cycle = await running(repository, queue)
        async with repository.transaction() as uow:
            await uow.request_cycle(cycle.incident_id, STARTED - timedelta(seconds=30))
        finished = done(cycle)
        event = IncidentEvent(
            cycle.incident_id, FINISHED, EventType.CYCLE_COMPLETED, {"number": 1}, "grolla"
        )
        card = make_notification(
            cycle.incident_id,
            NotificationKind.FOLLOW_UP_UPDATE,
            cycle=finished,
            impact="R$ 3.000,00 estimado",
        )
        usages = [
            fake_usage(1_000, STAGE_FOLLOW_UP),
            fake_usage(500, STAGE_FOLLOW_UP),
            fake_usage(9_000, STAGE_INVESTIGATION),
        ]

        await queue.finish(finished, [event], [card], usages, FINISHED)

        assert await repository.cycles_of(cycle.incident_id) == [finished]
        assert await repository.events_of(cycle.incident_id) == [event]
        due = [
            i.notification
            for i in await outbox.due(FINISHED, 10_000)
            if i.notification.incident_id == cycle.incident_id
        ]
        assert due == [card]
        found = await candidate(queue, cycle.incident_id)
        assert found is not None
        assert found.requested_at is None
        assert found.tokens == 1_100 + 550  # só a etapa follow_up conta
        assert cycle.incident_id not in {
            c.incident_id for c in await queue.stuck(FINISHED + timedelta(hours=1))
        }

    async def test_partial_cycle_keeps_the_reason_and_the_evaluations(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        cycle = await running(repository, queue)
        partial = replace(
            cycle,
            status=CycleStatus.PARTIAL,
            finished_at=FINISHED,
            evaluations=make_evaluations(),
            partial_reason=PartialReason.TIMEOUT,
        )

        await queue.finish(partial, [], [], [], FINISHED)

        assert await repository.cycles_of(cycle.incident_id) == [partial]

    async def test_finish_of_a_cycle_not_running_writes_nothing(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        cycle = await running(repository, queue)
        reaped = replace(
            cycle,
            status=CycleStatus.PARTIAL,
            finished_at=FINISHED,
            partial_reason=PartialReason.INTERRUPTED,
        )
        event = IncidentEvent(cycle.incident_id, FINISHED, EventType.CYCLE_COMPLETED, {})
        await queue.finish(reaped, [], [], [], FINISHED)
        await queue.finish(done(cycle), [event], [], [fake_usage(stage=STAGE_FOLLOW_UP)], FINISHED)
        pending = await saved(repository)
        await queue.enqueue(pending.id, 1, T0)
        never_claimed = Cycle(pending.id, 1, CycleStatus.PENDING, T0)
        await queue.finish(done(never_claimed), [event], [], [], FINISHED)

        assert await repository.cycles_of(cycle.incident_id) == [reaped]
        assert await repository.events_of(cycle.incident_id) == []
        found = await candidate(queue, cycle.incident_id)
        assert found is not None
        assert found.tokens == 0
        assert await repository.cycles_of(pending.id) == [never_claimed]

    async def test_cycles_of_are_in_number_order_in_any_state(
        self, repository: IncidentRepository, queue: CycleQueue
    ) -> None:
        cycle = await running(repository, queue)
        await queue.finish(done(cycle), [], [], [], FINISHED)
        await queue.enqueue(cycle.incident_id, 2, FINISHED + timedelta(minutes=5))

        cycles = await repository.cycles_of(cycle.incident_id)

        assert [(c.number, c.status) for c in cycles] == [
            (1, CycleStatus.COMPLETED),
            (2, CycleStatus.PENDING),
        ]
        assert await repository.cycles_of(uuid4()) == []


async def _drain_analyses(queue: AnalysisQueue) -> list[AnalysisJob]:
    jobs: list[AnalysisJob] = []
    while (job := await queue.claim(T0 + timedelta(seconds=5))) is not None:
        jobs.append(job)
    return jobs
