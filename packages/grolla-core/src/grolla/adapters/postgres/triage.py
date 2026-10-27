"""Triagem no PostgreSQL (migração 0011).

Contrato de menos ruído: os corpos são do grolla-persistencia.
"""

from collections.abc import Sequence
from datetime import datetime

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.investigation import LlmUsage
from grolla.application.triage import TriageJob
from grolla.domain.incidents import Incident, IncidentEvent, Notification


class PostgresTriageQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def claim(self, now: datetime) -> TriageJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[TriageJob]:
        raise NotImplementedError

    async def open_since(self, since: datetime) -> list[Incident]:
        raise NotImplementedError

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
        raise NotImplementedError
