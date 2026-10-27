"""Suíte de contrato de TriageQueue.

O adapter sobrescreve `repository`, `queue` e `outbox`, que precisam enxergar o mesmo
armazenamento: o pedido nasce do `request_triage` do unit of work, na transação que abre o
incidente. No banco real compartilhado, outros testes deixam pedidos pendentes: cada teste cria
incidentes novos (uuid4), esvazia a fila com `drain` e só confere os próprios pedidos.
O uso do modelo gravado pelo `finish` não tem leitura no port: o adapter testa à parte.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import STAGE_TRIAGE
from grolla.application.triage import TriageJob, TriageQueue
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import T0, make_incident, make_notification
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(seconds=30)


async def requested(
    repository: IncidentRepository, *, at: datetime = T0, incident: Incident | None = None
) -> Incident:
    """Abre um incidente novo com a triagem pedida em `at`."""
    incident = incident or make_incident(last_alert_at=at)
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_triage(incident.id, at)
    return incident


async def drain(queue: TriageQueue, now: datetime = STARTED) -> list[TriageJob]:
    jobs: list[TriageJob] = []
    while (job := await queue.claim(now)) is not None:
        jobs.append(job)
    return jobs


def mine(jobs: list[TriageJob], *incidents: UUID) -> list[TriageJob]:
    return [j for j in jobs if j.incident_id in incidents]


class TriageQueueContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> TriageQueue:
        raise NotImplementedError

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        raise NotImplementedError

    async def test_request_becomes_one_running_job_when_claimed(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        incident = await requested(repository)
        async with repository.transaction() as uow:
            await uow.request_triage(incident.id, T0 + timedelta(seconds=1))  # ignorado

        jobs = mine(await drain(queue), incident.id)

        assert jobs == [TriageJob(incident.id, requested_at=T0, started_at=STARTED)]

    async def test_claim_takes_the_oldest_request_first(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        later = await requested(repository, at=T0 + timedelta(seconds=2))
        earlier = await requested(repository, at=T0 + timedelta(seconds=1))

        jobs = mine(await drain(queue), earlier.id, later.id)

        assert [j.incident_id for j in jobs] == [earlier.id, later.id]

    async def test_stuck_lists_running_requests_started_before(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        incident = await requested(repository)
        (job,) = mine(await drain(queue), incident.id)

        assert job in await queue.stuck(STARTED + timedelta(seconds=1))
        assert job not in await queue.stuck(STARTED)

    async def test_finish_saves_incident_events_and_cards_and_ends_the_request(
        self, repository: IncidentRepository, queue: TriageQueue, outbox: NotificationOutbox
    ) -> None:
        incident = await requested(repository)
        (job,) = mine(await drain(queue), incident.id)
        raised = replace(incident, severity=Severity.P1, state=IncidentState.DETECTED)
        event = IncidentEvent(incident.id, FINISHED, EventType.TRIAGE_COMPLETED, {"merge": None})
        card = make_notification(incident.id, NotificationKind.SEVERITY_RAISED)

        await queue.finish(
            job,
            raised,
            events=[event],
            notifications=[card],
            usages=[fake_usage(800, STAGE_TRIAGE)],
            reason=None,
            at=FINISHED,
        )

        assert await repository.get_incident(incident.id) == raised
        assert await repository.events_of(incident.id) == [event]
        due = [
            i.notification
            for i in await outbox.due(FINISHED, 10_000)
            if i.notification.incident_id == incident.id
        ]
        assert due == [card]
        assert job not in await queue.stuck(FINISHED + timedelta(hours=1))
        async with repository.transaction() as uow:
            await uow.request_triage(incident.id, FINISHED)  # pedido repetido é ignorado
        assert mine(await drain(queue, FINISHED), incident.id) == []

    async def test_failed_request_keeps_the_incident_and_is_not_claimed_again(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        incident = await requested(repository)
        (job,) = mine(await drain(queue), incident.id)

        await queue.finish(
            job,
            None,
            events=[],
            notifications=[],
            usages=[],
            reason="LLM indisponível",
            at=FINISHED,
        )

        assert await repository.get_incident(incident.id) == incident
        assert job not in await queue.stuck(FINISHED + timedelta(hours=1))
        assert mine(await drain(queue, FINISHED), incident.id) == []

    async def test_finish_only_counts_while_running(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        incident = await requested(repository)
        (job,) = mine(await drain(queue), incident.id)
        await queue.finish(
            job, None, events=[], notifications=[], usages=[], reason=None, at=FINISHED
        )
        late = replace(incident, severity=Severity.P0)
        event = IncidentEvent(incident.id, FINISHED, EventType.TRIAGE_COMPLETED, {})

        await queue.finish(
            job, late, events=[event], notifications=[], usages=[], reason=None, at=FINISHED
        )

        assert await repository.get_incident(incident.id) == incident
        assert await repository.events_of(incident.id) == []

    async def test_open_since_lists_open_incidents_with_a_recent_alert(
        self, repository: IncidentRepository, queue: TriageQueue
    ) -> None:
        since = T0 + timedelta(minutes=10)
        recent = make_incident(last_alert_at=since)
        old = make_incident(last_alert_at=since - timedelta(seconds=1))
        closed = make_incident(state=IncidentState.RESOLVED, last_alert_at=since)
        async with repository.transaction() as uow:
            for incident in (recent, old, closed):
                await uow.save(incident)

        found = [i for i in await queue.open_since(since) if i.id in {recent.id, old.id, closed.id}]

        assert found == [recent]
