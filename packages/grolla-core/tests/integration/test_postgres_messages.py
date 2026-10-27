"""Contagem das mensagens por pessoa sobre Postgres real: chat do war room e console juntos."""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresConsoleStore,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresMessageCounter,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage
from grolla.domain.console import ConsoleMessage, ConsoleSession
from grolla.testing.builders import T0, make_incident
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"messages_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


async def test_sent_since_joins_chat_and_console_of_the_author_in_order(
    database: PostgresDatabase,
) -> None:
    ana, bia = f"Ana {uuid4().hex[:6]}", f"Bia {uuid4().hex[:6]}"
    repository = PostgresIncidentRepository(database)
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        chat = [
            (ana, T0 - timedelta(seconds=1)),  # antes da janela
            (ana, T0),
            (ana, T0 + timedelta(minutes=3)),
            (bia, T0 + timedelta(minutes=1)),
            (AGENT_AUTHOR, T0 + timedelta(minutes=1)),
        ]
        for author, at in chat:
            await uow.add_message(ChatMessage(0, incident.id, author, "oi", at), wants_reply=False)
    console = PostgresConsoleStore(database)
    session = ConsoleSession(uuid4(), "oi", ana, T0, T0)
    at = T0 + timedelta(minutes=2)
    await console.create_session(session, ConsoleMessage(0, session.id, ana, "oi", at))

    sent = await PostgresMessageCounter(database).sent_since(ana, T0)

    assert sent == [T0, T0 + timedelta(minutes=2), T0 + timedelta(minutes=3)]
    assert await PostgresMessageCounter(database).sent_since(f"Caio {uuid4().hex}", T0) == []
