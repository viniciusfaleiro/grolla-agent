"""Ciclos de acompanhamento no PostgreSQL (migração 0008).

Contrato do agente que acompanha a crise: os corpos são do grolla-persistencia.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.follow_up import CycleCandidate
from grolla.application.investigation import LlmUsage
from grolla.domain.follow_up import Cycle
from grolla.domain.incidents import IncidentEvent, Notification, Severity


class PostgresCycleQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

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
