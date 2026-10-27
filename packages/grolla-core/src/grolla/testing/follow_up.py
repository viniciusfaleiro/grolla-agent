"""Fake em memória do `CycleQueue`, sobre o estado de um `InMemoryIncidentRepository`.

Contrato do agente que acompanha a crise: os corpos são do grolla-dominio.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from grolla.application.follow_up import CycleCandidate
from grolla.application.investigation import LlmUsage
from grolla.domain.follow_up import Cycle
from grolla.domain.incidents import IncidentEvent, Notification, Severity
from grolla.testing.incidents import InMemoryIncidentRepository


class InMemoryCycleQueue:
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository

    async def candidates(self, severities: Collection[Severity]) -> list[CycleCandidate]:
        raise NotImplementedError

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        raise NotImplementedError

    async def claim(self, now: datetime) -> Cycle | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[Cycle]:
        raise NotImplementedError

    async def finish(
        self,
        cycle: Cycle,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        raise NotImplementedError
