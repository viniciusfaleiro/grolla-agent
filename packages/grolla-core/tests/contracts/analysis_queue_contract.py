"""Suíte de contrato de AnalysisQueue e do pedido de análise (`request_analysis`, `analysis_of`).

As fixtures `repository`, `queue` e `outbox` precisam enxergar o mesmo armazenamento. No banco
real compartilhado, outros testes deixam pedidos pendentes: por isso cada teste esvazia a fila
com `drain` e só confere os pedidos dos próprios incidentes, que são novos (uuid4); a suíte
não assume que a fila começa vazia.
O uso do modelo gravado pelo `finish` não tem leitura no port: o adapter testa à parte.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.application.analysis import AnalysisJob, AnalysisQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.domain.analysis import AnalysisStatus, PartialReason
from grolla.domain.incidents import EventType, IncidentEvent, NotificationKind
from grolla.testing.builders import T0, make_analysis, make_incident, make_notification
from grolla.testing.investigation import fake_usage


async def requested(repository: IncidentRepository, at: datetime = T0) -> UUID:
    """Abre um incidente novo com o pedido de análise pendente desde `at`."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_analysis(incident.id, at)
    return incident.id


async def drain(queue: AnalysisQueue, now: datetime) -> list[AnalysisJob]:
    """Reivindica tudo que está pendente, na ordem em que a fila entrega."""
    jobs: list[AnalysisJob] = []
    while (job := await queue.claim(now)) is not None:
        jobs.append(job)
    return jobs


def ids(jobs: list[AnalysisJob]) -> list[UUID]:
    return [job.incident_id for job in jobs]


class AnalysisQueueContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> AnalysisQueue:
        raise NotImplementedError

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        raise NotImplementedError

    async def test_claim_marks_the_request_running(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        incident_id = await requested(repository)
        now = T0 + timedelta(seconds=5)

        jobs = [job for job in await drain(queue, now) if job.incident_id == incident_id]

        assert jobs == [AnalysisJob(incident_id, requested_at=T0, started_at=now)]
        analysis = await repository.analysis_of(incident_id)
        assert analysis is not None
        assert analysis.status is AnalysisStatus.RUNNING
        assert analysis.started_at == now

    async def test_claimed_request_is_not_claimed_again(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        incident_id = await requested(repository)
        await drain(queue, T0)

        assert await queue.claim(T0) is None
        assert incident_id not in ids(await drain(queue, T0))

    async def test_oldest_request_is_claimed_first(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        newer = await requested(repository, at=T0 + timedelta(seconds=1))
        older = await requested(repository, at=T0)

        claimed = [i for i in ids(await drain(queue, T0)) if i in {newer, older}]

        assert claimed == [older, newer]

    async def test_stuck_lists_requests_running_since_before_the_limit(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        incident_id = await requested(repository)
        await drain(queue, T0)

        assert incident_id in ids(await queue.stuck(T0 + timedelta(minutes=1)))
        assert incident_id not in ids(await queue.stuck(T0))

    async def test_finish_writes_analysis_events_and_card(
        self,
        repository: IncidentRepository,
        queue: AnalysisQueue,
        outbox: NotificationOutbox,
    ) -> None:
        incident_id = await requested(repository)
        await drain(queue, T0)
        done = T0 + timedelta(minutes=2)
        analysis = make_analysis(incident_id, requested_at=T0, started_at=T0, finished_at=done)
        events = [IncidentEvent(incident_id, done, EventType.ANALYSIS_COMPLETED, {"hipoteses": 2})]
        card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

        await queue.finish(analysis, events, card, [fake_usage(), fake_usage(500)], done)

        assert await repository.analysis_of(incident_id) == analysis
        assert await repository.events_of(incident_id) == events
        due = [
            i for i in await outbox.due(done, 10_000) if i.notification.incident_id == incident_id
        ]
        assert [item.notification for item in due] == [card]
        assert incident_id not in ids(await queue.stuck(done + timedelta(hours=1)))

    async def test_finished_partial_analysis_keeps_the_reason(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        incident_id = await requested(repository)
        await drain(queue, T0)
        analysis = make_analysis(
            incident_id,
            status=AnalysisStatus.PARTIAL,
            requested_at=T0,
            partial_reason=PartialReason.INTERRUPTED,
        )
        card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

        await queue.finish(analysis, [], card, [], T0 + timedelta(minutes=8))

        assert await repository.analysis_of(incident_id) == analysis

    async def test_finish_of_an_already_finished_request_writes_nothing(
        self,
        repository: IncidentRepository,
        queue: AnalysisQueue,
        outbox: NotificationOutbox,
    ) -> None:
        incident_id = await requested(repository)
        await drain(queue, T0)
        reaped = make_analysis(
            incident_id,
            status=AnalysisStatus.PARTIAL,
            requested_at=T0,
            partial_reason=PartialReason.INTERRUPTED,
        )
        late = make_analysis(incident_id, requested_at=T0)
        first = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=reaped)
        second = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=late)
        event = IncidentEvent(incident_id, T0, EventType.ANALYSIS_COMPLETED, {})
        at = T0 + timedelta(minutes=8)

        await queue.finish(reaped, [], first, [], at)
        await queue.finish(late, [event], second, [fake_usage()], at)

        assert await repository.analysis_of(incident_id) == reaped
        assert await repository.events_of(incident_id) == []
        due = [i for i in await outbox.due(at, 10_000) if i.notification.incident_id == incident_id]
        assert [item.notification for item in due] == [first]

    async def test_finish_of_a_request_never_claimed_writes_nothing(
        self, repository: IncidentRepository, queue: AnalysisQueue
    ) -> None:
        incident = make_incident()
        async with repository.transaction() as uow:
            await uow.save(incident)
        analysis = make_analysis(incident.id)
        card = make_notification(incident.id, NotificationKind.ANALYSIS_READY, analysis=analysis)

        await queue.finish(analysis, [], card, [], T0)

        assert await repository.analysis_of(incident.id) is None

    async def test_requested_analysis_is_pending_and_requested_once(
        self, repository: IncidentRepository
    ) -> None:
        incident = make_incident()

        async with repository.transaction() as uow:
            await uow.save(incident)
            await uow.request_analysis(incident.id, T0)
        async with repository.transaction() as uow:
            await uow.request_analysis(incident.id, T0 + timedelta(minutes=1))

        assert await repository.analysis_of(incident.id) == make_analysis(
            incident.id, status=AnalysisStatus.PENDING, requested_at=T0
        )

    async def test_analysis_never_requested_is_none(self, repository: IncidentRepository) -> None:
        incident = make_incident()
        async with repository.transaction() as uow:
            await uow.save(incident)

        assert await repository.analysis_of(incident.id) is None

    async def test_failed_transaction_requests_no_analysis(
        self, repository: IncidentRepository
    ) -> None:
        incident = make_incident()
        async with repository.transaction() as uow:
            await uow.save(incident)

        async def request_then_fail() -> None:
            async with repository.transaction() as uow:
                await uow.request_analysis(incident.id, T0)
                raise RuntimeError("falha no meio")

        with pytest.raises(RuntimeError, match="falha no meio"):
            await request_then_fail()

        assert await repository.analysis_of(incident.id) is None
