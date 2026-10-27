"""Console de operação no Postgres: o que só o banco real mostra (jsonb, SKIP LOCKED e o uso
sem incidente em `llm_usage`).
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.console_store_contract import ConsoleStoreContract
from grolla.adapters.postgres import (
    PostgresConsoleStore,
    PostgresDatabase,
    PostgresIncidentRepository,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.console import ConsoleStore
from grolla.application.investigation import STAGE_CONSOLE, STAGE_WARROOM_CHAT
from grolla.domain.chat import AGENT_AUTHOR, ReplyStatus
from grolla.domain.console import ChartRequest, ConsoleMessage, ConsoleSession
from grolla.testing.builders import T0, make_evaluations, make_incident
from grolla.testing.investigation import fake_usage
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(seconds=5)
CHART = ChartRequest(service="checkout", signal="taxa_de_erro", minutes=30)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"console_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


class TestPostgresConsoleStoreContract(ConsoleStoreContract):
    @pytest.fixture
    def store(self, database: PostgresDatabase) -> ConsoleStore:
        return PostgresConsoleStore(database)

    @pytest.fixture
    async def incident_id(self, database: PostgresDatabase) -> UUID:
        incident = make_incident()
        async with PostgresIncidentRepository(database).transaction() as uow:
            await uow.save(incident)
        return incident.id


@pytest.fixture
def store(database: PostgresDatabase) -> PostgresConsoleStore:
    return PostgresConsoleStore(database)


async def drain(store: PostgresConsoleStore) -> None:
    while await store.claim(STARTED) is not None:
        pass


async def started(store: PostgresConsoleStore, *questions: str) -> tuple[UUID, list[int]]:
    """Sessão nova com uma pergunta pendente por texto."""
    session_id = uuid4()
    first, *rest = questions
    asked = [
        await store.create_session(
            ConsoleSession(session_id, first, "Ana", T0, T0),
            ConsoleMessage(0, session_id, "Ana", first, T0),
        )
    ]
    asked += [await store.add_question(ConsoleMessage(0, session_id, "Ana", q, T0)) for q in rest]
    return session_id, [m.id for m in asked]


async def test_reply_keeps_charts_and_evaluations_and_records_usage_without_incident(
    store: PostgresConsoleStore, database: PostgresDatabase
) -> None:
    await drain(store)
    session_id, _ = await started(store, "como está o checkout?")
    job = await store.claim(STARTED)
    assert job is not None
    reply = ConsoleMessage(
        0,
        session_id,
        AGENT_AUTHOR,
        "Erro em 0,2%.",
        T0 + timedelta(seconds=30),
        charts=(CHART,),
        evaluations=make_evaluations(),
    )

    await store.answer(job, reply, [fake_usage(1_000, STAGE_CONSOLE)], STARTED)

    question, answer = await store.messages(session_id)
    assert question.reply_status is ReplyStatus.ANSWERED
    assert answer.charts == (CHART,)
    assert answer.evaluations == make_evaluations()
    assert answer.reply_to == question.id
    assert answer.reply_status is None
    async with database.engine.connect() as connection:
        incidents = (
            await connection.execute(
                sa.text("SELECT incident_id FROM llm_usage WHERE stage = :stage"),
                {"stage": STAGE_CONSOLE},
            )
        ).scalars()
        assert set(incidents) == {None}


async def test_tokens_since_counts_only_the_console_stage_in_the_window(
    store: PostgresConsoleStore,
) -> None:
    await drain(store)
    since = T0 + timedelta(days=30)
    before = await store.tokens_since(since)
    await started(store, "pergunta 1", "pergunta 2")
    first, second = await store.claim(STARTED), await store.claim(STARTED)
    assert first is not None
    assert second is not None
    reply = ConsoleMessage(0, first.session_id, AGENT_AUTHOR, "ok", STARTED)

    await store.answer(first, reply, [fake_usage(1_000, STAGE_CONSOLE)], since)
    await store.answer(second, reply, [fake_usage(1_000, STAGE_WARROOM_CHAT)], since)
    await store.answer(first, reply, [fake_usage(1_000, STAGE_CONSOLE)], since)  # já respondida

    assert await store.tokens_since(since) == before + 1_100
    assert await store.tokens_since(since + timedelta(seconds=1)) == 0


async def test_simultaneous_claims_never_take_the_same_question(
    store: PostgresConsoleStore,
) -> None:
    await drain(store)
    _, pending = await started(store, *(f"pergunta {n}" for n in range(PARALLEL_CLAIMS // 2)))

    jobs = await asyncio.gather(*(store.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    taken = [job.message_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(pending)  # cada pergunta uma vez, e nenhuma perdida
