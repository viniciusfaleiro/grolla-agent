"""Fake em memória do `TriageQueue`, sobre o estado de um `InMemoryIncidentRepository`."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grolla.application.investigation import LlmUsage
from grolla.application.triage import TriageJob
from grolla.domain.incidents import OPEN_STATES, Incident, IncidentEvent, Notification
from grolla.testing.incidents import InMemoryIncidentRepository

PENDING = "pendente"
RUNNING = "em_andamento"
DONE = "feito"
FAILED = "falho"


@dataclass
class _Job:
    incident_id: UUID
    requested_at: datetime
    status: str = PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None

    def job(self, started_at: datetime) -> TriageJob:
        return TriageJob(self.incident_id, self.requested_at, started_at)


class InMemoryTriageQueue:
    """O pedido da triagem (`triage_requests`, gravado na transação que abre o incidente) vira
    um pedido pendente, um só por incidente. O estado é público para os testes inspecionarem.
    """

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository
        self.jobs: dict[UUID, _Job] = {}

    def _sync(self) -> None:
        for incident_id, at in self.repository.triage_requests.items():
            self.jobs.setdefault(incident_id, _Job(incident_id, at))

    async def claim(self, now: datetime) -> TriageJob | None:
        self._sync()
        pending = [j for j in self.jobs.values() if j.status == PENDING]
        if not pending:
            return None
        job = min(pending, key=lambda j: j.requested_at)
        job.status, job.started_at = RUNNING, now
        return job.job(now)

    async def stuck(self, started_before: datetime) -> list[TriageJob]:
        return [
            j.job(j.started_at)
            for j in self.jobs.values()
            if j.status == RUNNING and j.started_at is not None and j.started_at < started_before
        ]

    async def open_since(self, since: datetime) -> list[Incident]:
        return [
            i
            for i in self.repository.incidents.values()
            if i.state in OPEN_STATES and i.last_alert_at >= since
        ]

    async def finish(  # noqa: PLR0913 — mesmo port
        self,
        job: TriageJob,
        incident: Incident | None,
        *,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        row = self.jobs.get(job.incident_id)
        if row is None or row.status != RUNNING:
            return
        async with self.repository.transaction() as uow:
            if incident is not None:
                await uow.save(incident)
            await uow.append(events)
            for notification in notifications:
                await uow.enqueue(notification, at)
        self.repository.usages.extend((job.incident_id, u, at) for u in usages)
        row.status = DONE if reason is None else FAILED
        row.finished_at, row.reason = at, reason
