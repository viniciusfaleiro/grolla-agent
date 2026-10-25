"""Fila de análises e registro de uso do modelo de linguagem sobre a migração 0003."""

from collections.abc import Sequence
from datetime import datetime

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.analysis import AnalysisJob
from grolla.application.investigation import LlmUsage
from grolla.domain.analysis import Analysis
from grolla.domain.incidents import IncidentEvent, Notification


class PostgresAnalysisQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> AnalysisJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        raise NotImplementedError

    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        raise NotImplementedError
