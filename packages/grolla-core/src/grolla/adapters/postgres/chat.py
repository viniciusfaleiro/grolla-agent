"""Fila das perguntas do chat do war room sobre a migração 0005."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.chat import ChatJob
from grolla.application.investigation import LlmUsage
from grolla.domain.chat import ChatMessage
from grolla.domain.incidents import IncidentEvent


class PostgresChatQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> ChatJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[ChatJob]:
        raise NotImplementedError

    async def answer(
        self,
        job: ChatJob,
        reply: ChatMessage,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        raise NotImplementedError

    async def chat_tokens(self, incident_id: UUID) -> int:
        raise NotImplementedError
