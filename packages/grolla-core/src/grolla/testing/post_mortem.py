"""Fakes do post mortem: `PostMortemStore` em memória e um `DocumentRenderer` que não gera PDF
de verdade.

Contrato do post mortem: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from grolla.application.investigation import LlmUsage
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob, PostMortemSummary
from grolla.domain.incidents import Incident
from grolla.domain.post_mortem import PostMortem, SectionKey, SectionVersion
from grolla.testing.incidents import InMemoryIncidentRepository


class InMemoryPostMortemStore:
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository

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


class FakeDocumentRenderer:
    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        raise NotImplementedError
