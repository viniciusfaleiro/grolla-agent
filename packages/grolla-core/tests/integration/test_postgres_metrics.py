"""Fatos das métricas do agente sobre Postgres real.

A leitura vê todos os incidentes, post mortems e eventos do banco (e o log não aceita TRUNCATE),
então cada teste usa um banco próprio dentro do container da sessão. As recusas (do chat e do
console) ficam fora da suíte de contrato, que não tem as mensagens.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from contracts.metrics_reader_contract import SINCE, MetricsReaderContract
from grolla.adapters.postgres import (
    PostgresConsoleStore,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresMetricsReader,
    PostgresPostMortemStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.incidents import IncidentRepository
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import PostMortemStore
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage
from grolla.domain.console import ConsoleMessage, ConsoleSession
from grolla.domain.safety import Scope
from grolla.testing.builders import make_incident
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"metrics_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


class TestPostgresMetricsReaderContract(MetricsReaderContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def post_mortems(self, database: PostgresDatabase) -> PostMortemStore:
        return PostgresPostMortemStore(database)

    @pytest.fixture
    def reader(self, database: PostgresDatabase) -> MetricsReader:
        return PostgresMetricsReader(database)


async def test_refusals_are_the_refused_replies_of_chat_and_console_since(
    database: PostgresDatabase,
) -> None:
    repository = PostgresIncidentRepository(database)
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        replies = [
            (SINCE + timedelta(minutes=1), Scope.OFF_TOPIC),
            (SINCE - timedelta(seconds=1), Scope.EXECUTION),  # antes do período
            (SINCE + timedelta(minutes=2), None),
        ]
        for at, refusal in replies:
            reply = ChatMessage(0, incident.id, AGENT_AUTHOR, "x", at, refusal=refusal)
            await uow.add_message(reply, wants_reply=False)
    console = PostgresConsoleStore(database)
    session = ConsoleSession(uuid4(), "oi", "Ana", SINCE, SINCE)
    question = ConsoleMessage(0, session.id, "Ana", "oi", SINCE)
    await console.create_session(session, question)
    job = await console.claim(SINCE)
    assert job is not None
    at = SINCE + timedelta(minutes=3)
    reply = ConsoleMessage(0, session.id, AGENT_AUTHOR, "x", at, refusal=Scope.EXECUTION)
    await console.answer(job, reply, [], at)

    facts = await PostgresMetricsReader(database).facts(SINCE)

    assert facts.refusals == (SINCE + timedelta(minutes=1), at)
