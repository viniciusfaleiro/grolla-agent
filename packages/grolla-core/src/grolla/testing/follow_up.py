"""Fake em memória do `CycleQueue`, sobre o estado de um `InMemoryIncidentRepository`.

Os ciclos ficam em `repository.cycles`, o pedido antecipado em `repository.cycle_requests` e o
uso do modelo em `repository.usages`.
"""

from collections.abc import Collection, Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from grolla.application.follow_up import CycleCandidate
from grolla.application.investigation import STAGE_FOLLOW_UP, LlmUsage
from grolla.domain.analysis import AnalysisStatus
from grolla.domain.follow_up import Cycle, CycleStatus
from grolla.domain.incidents import OPEN_STATES, IncidentEvent, Notification, Severity
from grolla.testing.incidents import InMemoryIncidentRepository

DONE = frozenset({AnalysisStatus.COMPLETED, AnalysisStatus.PARTIAL})


class InMemoryCycleQueue:
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository

    async def candidates(self, severities: Collection[Severity]) -> list[CycleCandidate]:
        found: list[CycleCandidate] = []
        for incident in self.repository.incidents.values():
            if incident.state not in OPEN_STATES or incident.severity not in severities:
                continue
            analysis = self.repository.analyses.get(incident.id)
            done = analysis.finished_at if analysis and analysis.status in DONE else None
            found.append(
                CycleCandidate(
                    incident=incident,
                    analysis_done_at=done,
                    cycles=tuple(await self.repository.cycles_of(incident.id)),
                    requested_at=self.repository.cycle_requests.get(incident.id),
                    tokens=self._tokens(incident.id),
                )
            )
        return found

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        key = (incident_id, number)
        if key in self.repository.cycles:
            return False
        self.repository.cycles[key] = Cycle(incident_id, number, CycleStatus.PENDING, at)
        return True

    async def claim(self, now: datetime) -> Cycle | None:
        pending = [c for c in self.repository.cycles.values() if c.status is CycleStatus.PENDING]
        oldest = min(pending, key=lambda c: c.requested_at, default=None)
        if oldest is None:
            return None
        running = replace(oldest, status=CycleStatus.RUNNING, started_at=now)
        self.repository.cycles[(oldest.incident_id, oldest.number)] = running
        return running

    async def stuck(self, started_before: datetime) -> list[Cycle]:
        return [
            c
            for c in self.repository.cycles.values()
            if c.status is CycleStatus.RUNNING and c.started_at and c.started_at < started_before
        ]

    async def finish(
        self,
        cycle: Cycle,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        key = (cycle.incident_id, cycle.number)
        current = self.repository.cycles.get(key)
        if current is None or current.status is not CycleStatus.RUNNING:
            return  # já encerrado (pelo reaper ou por outro worker): o ciclo chega uma vez
        async with self.repository.transaction() as uow:
            self.repository.cycles[key] = cycle
            self.repository.cycle_requests.pop(cycle.incident_id, None)
            await uow.append(events)
            for notification in notifications:
                await uow.enqueue(notification, at)
            self.repository.usages.extend((cycle.incident_id, u, at) for u in usages)

    def _tokens(self, incident_id: UUID) -> int:
        return sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
            for owner, u, _ in self.repository.usages
            if owner == incident_id and u.stage == STAGE_FOLLOW_UP
        )
