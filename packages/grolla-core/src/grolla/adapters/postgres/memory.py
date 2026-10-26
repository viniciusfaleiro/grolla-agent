"""Memória viva no PostgreSQL: registros com busca textual em português, versões, a fila do
aprendizado e as rodadas de estudo.

Contrato da memória viva: os corpos são do grolla-persistencia.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.investigation import LlmUsage
from grolla.application.memory import (
    LearningFailure,
    LearningJob,
    ServiceStudy,
    StudyMode,
    StudyRun,
    StudyStatus,
)
from grolla.application.memory_store import SubjectSummary
from grolla.domain.memory import Memory, MemoryContext, MemoryVersion


class PostgresMemoryStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def context_for(
        self, services: Sequence[str], alert_names: Sequence[str], text: str
    ) -> MemoryContext:
        raise NotImplementedError

    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]:
        raise NotImplementedError

    async def get(self, memory_id: UUID) -> Memory | None:
        raise NotImplementedError

    async def dossier(self, subject: str) -> Memory | None:
        raise NotImplementedError

    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None:
        raise NotImplementedError

    async def all_active(self) -> list[Memory]:
        raise NotImplementedError

    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        raise NotImplementedError

    async def subjects(self) -> list[SubjectSummary]:
        raise NotImplementedError

    async def of_subject(self, subject: str) -> list[Memory]:
        raise NotImplementedError

    async def versions(self, memory_id: UUID) -> list[MemoryVersion]:
        raise NotImplementedError


class PostgresLearningQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def claim(self, now: datetime) -> LearningJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[LearningJob]:
        raise NotImplementedError

    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        raise NotImplementedError

    async def failed(self) -> list[LearningFailure]:
        raise NotImplementedError


class PostgresStudyQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def request(self, mode: StudyMode, author: str, now: datetime) -> bool:
        raise NotImplementedError

    async def claim(self, now: datetime) -> StudyRun | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[StudyRun]:
        raise NotImplementedError

    async def service_order(self, services: Sequence[str], mode: StudyMode) -> list[str]:
        raise NotImplementedError

    async def service_done(
        self, run: StudyRun, study: ServiceStudy, usages: Sequence[LlmUsage]
    ) -> None:
        raise NotImplementedError

    async def finish(
        self, run: StudyRun, status: StudyStatus, reason: str | None, now: datetime
    ) -> None:
        raise NotImplementedError

    async def last(self) -> StudyRun | None:
        raise NotImplementedError

    async def services(self) -> list[ServiceStudy]:
        raise NotImplementedError
