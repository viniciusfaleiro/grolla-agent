"""Post mortem no PostgreSQL (migração 0009): documento, versões das seções e a fila de
rascunhos e regenerações.

Contrato do post mortem: os corpos são do grolla-persistencia.
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.investigation import LlmUsage
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob, PostMortemSummary
from grolla.domain.post_mortem import PostMortem, SectionKey, SectionVersion


class PostgresPostMortemStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def get(self, incident_id: UUID) -> PostMortem | None:
        raise NotImplementedError

    async def save(self, pm: PostMortem, versions: Sequence[SectionVersion]) -> None:
        raise NotImplementedError

    async def versions(self, incident_id: UUID, key: SectionKey) -> list[SectionVersion]:
        raise NotImplementedError

    async def summaries(self, filters: PostMortemFilters, today: date) -> list[PostMortemSummary]:
        raise NotImplementedError

    async def request_regeneration(
        self,
        incident_id: UUID,
        key: SectionKey,
        *,
        instruction: str | None,
        author: str,
        at: datetime,
    ) -> None:
        raise NotImplementedError

    async def claim(self, now: datetime) -> PostMortemJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[PostMortemJob]:
        raise NotImplementedError

    async def finish(  # noqa: PLR0913 — mesmo port
        self,
        job: PostMortemJob,
        pm: PostMortem | None,
        *,
        versions: Sequence[SectionVersion],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        raise NotImplementedError
