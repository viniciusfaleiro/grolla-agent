"""War room sobre Postgres real: suítes de contrato, campos novos do incidente e concorrência.

A fila do chat pega a pergunta pendente mais antiga de todo o banco, então este módulo usa um
banco próprio dentro do container da sessão.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.chat_queue_contract import ChatQueueContract, WarRoomRepositoryContract
from grolla.adapters.postgres import (
    PostgresChatQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.chat import ChatQueue
from grolla.application.incidents import IncidentRepository
from grolla.domain.chat import ChatMessage
from grolla.domain.incidents import IncidentState
from grolla.testing.builders import T0, make_incident
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(seconds=5)
LOCK_WAIT = 0.3  # segundos: tempo de a segunda transação mostrar que está esperando


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"war_room_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


class TestPostgresWarRoomRepositoryContract(WarRoomRepositoryContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)


class TestPostgresChatQueueContract(ChatQueueContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def queue(self, database: PostgresDatabase) -> ChatQueue:
        return PostgresChatQueue(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def queue(database: PostgresDatabase) -> PostgresChatQueue:
    return PostgresChatQueue(database)


async def saved(repository: PostgresIncidentRepository, **fields: object) -> UUID:
    incident = replace(make_incident(), **fields)
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident.id


async def test_new_incident_fields_round_trip(repository: PostgresIncidentRepository) -> None:
    incident_id = await saved(
        repository,
        state=IncidentState.RESOLVED,
        commander="Ana",
        handling_at=T0 + timedelta(minutes=2),
        mitigated_at=T0 + timedelta(minutes=10),
        resolved_at=T0 + timedelta(minutes=30),
    )

    found = await repository.get_incident(incident_id)

    assert found is not None
    assert found.commander == "Ana"
    assert found.handling_at == T0 + timedelta(minutes=2)
    assert found.mitigated_at == T0 + timedelta(minutes=10)
    assert found.resolved_at == T0 + timedelta(minutes=30)
    assert found in await repository.list_incidents(10_000)


async def test_simultaneous_actions_on_an_incident_are_serialized(
    repository: PostgresIncidentRepository,
) -> None:
    incident_id = await saved(repository, commander=None)
    second_started = asyncio.Event()

    async def second() -> str | None:
        async with repository.transaction() as uow:
            second_started.set()
            incident = await uow.incident_for_update(incident_id)
            assert incident is not None
            return incident.commander

    async with repository.transaction() as uow:
        first = await uow.incident_for_update(incident_id)
        assert first is not None
        waiting = asyncio.create_task(second())
        await second_started.wait()
        await asyncio.sleep(LOCK_WAIT)
        assert not waiting.done()  # a segunda ação espera o commit da primeira
        await uow.save(replace(first, commander="Ana"))

    assert await waiting == "Ana"  # e decide sobre o estado já atualizado


async def test_simultaneous_claims_never_take_the_same_question(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    while await queue.claim(STARTED) is not None:  # fila vazia: só as perguntas deste teste
        pass
    incident_id = await saved(repository)
    async with repository.transaction() as uow:
        pending = {
            (
                await uow.add_message(
                    ChatMessage(0, incident_id, "Ana", f"pergunta {n}", T0), wants_reply=True
                )
            ).id
            for n in range(PARALLEL_CLAIMS // 2)
        }

    jobs = await asyncio.gather(*(queue.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    taken = [job.message_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(pending)  # cada pergunta uma vez, e nenhuma perdida
