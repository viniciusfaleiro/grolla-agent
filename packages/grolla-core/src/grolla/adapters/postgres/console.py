"""Console de operação no PostgreSQL (migração 0012): sessões, mensagens e a fila de respostas.

Contrato do console de operação: os corpos são do grolla-persistencia.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.console import ConsoleJob
from grolla.application.investigation import LlmUsage
from grolla.domain.console import ConsoleMessage, ConsoleSession


class PostgresConsoleStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def create_session(
        self, session: ConsoleSession, question: ConsoleMessage
    ) -> ConsoleMessage:
        raise NotImplementedError

    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage:
        raise NotImplementedError

    async def sessions(self, limit: int) -> list[ConsoleSession]:
        raise NotImplementedError

    async def session(self, session_id: UUID) -> ConsoleSession | None:
        raise NotImplementedError

    async def messages(self, session_id: UUID) -> list[ConsoleMessage]:
        raise NotImplementedError

    async def link_incident(self, session_id: UUID, incident_id: UUID) -> None:
        raise NotImplementedError

    async def claim(self, now: datetime) -> ConsoleJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[ConsoleJob]:
        raise NotImplementedError

    async def answer(
        self, job: ConsoleJob, reply: ConsoleMessage, usages: Sequence[LlmUsage], at: datetime
    ) -> None:
        raise NotImplementedError

    async def tokens_since(self, since: datetime) -> int:
        raise NotImplementedError
