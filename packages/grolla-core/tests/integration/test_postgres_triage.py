"""Fila da triagem sobre Postgres real.

Herda `TriageQueueContract`; os testes daqui cobrem o que o port não lê: o status e o motivo
gravados, o `llm_usage`, a concorrência do `claim` e a atomicidade do `finish`. A fila pega o
pedido pendente mais antigo de todo o banco, então o módulo usa um banco próprio.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.triage_queue_contract import TriageQueueContract
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    PostgresTriageQueue,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import STAGE_TRIAGE, LlmUsage
from grolla.application.triage import TriageJob, TriageQueue
from grolla.domain.incidents import (
    EventType,
    IncidentEvent,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import T0, make_incident, make_notification
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(minutes=1)
USAGE = LlmUsage(
    stage=STAGE_TRIAGE,
    model="modelo-x",
    input_tokens=900,
    output_tokens=120,
    cache_read_tokens=0,
    cache_creation_tokens=0,
)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"triagens_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


@pytest.fixture
async def clean(database: PostgresDatabase) -> None:
    """Cada teste começa sem pedidos na fila."""
    async with database.engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM triages"))


class TestPostgresTriageQueueContract(TriageQueueContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def queue(self, database: PostgresDatabase) -> TriageQueue:
        return PostgresTriageQueue(database)

    @pytest.fixture
    def outbox(self, database: PostgresDatabase) -> NotificationOutbox:
        return PostgresNotificationOutbox(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def queue(database: PostgresDatabase) -> PostgresTriageQueue:
    return PostgresTriageQueue(database)


async def requested(repository: PostgresIncidentRepository, offset_seconds: int = 0) -> UUID:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_triage(incident.id, T0 + timedelta(seconds=offset_seconds))
    return incident.id


async def triage_row(database: PostgresDatabase, incident_id: UUID) -> tuple[Any, ...]:
    async with database.engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text(
                    "SELECT status, requested_at, started_at, finished_at, reason"
                    " FROM triages WHERE incident_id = :id"
                ),
                {"id": incident_id},
            )
        ).one()
    return tuple(row)


async def counts(database: PostgresDatabase, incident_id: UUID) -> tuple[int, int]:
    """Cards na outbox e registros de uso do modelo do incidente."""
    async with database.engine.connect() as connection:
        cards = await connection.execute(
            sa.text("SELECT count(*) FROM outbox WHERE incident_id = :id"), {"id": incident_id}
        )
        usages = await connection.execute(
            sa.text("SELECT count(*) FROM llm_usage WHERE incident_id = :id"), {"id": incident_id}
        )
    return cards.scalar_one(), usages.scalar_one()


@pytest.mark.usefixtures("clean")
async def test_request_is_pending_and_a_second_request_keeps_the_first(
    repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    incident_id = await requested(repository)
    async with repository.transaction() as uow:
        await uow.request_triage(incident_id, T0 + timedelta(minutes=1))

    assert await triage_row(database, incident_id) == ("pendente", T0, None, None, None)


@pytest.mark.usefixtures("clean")
async def test_simultaneous_claims_never_take_the_same_request(
    repository: PostgresIncidentRepository, queue: PostgresTriageQueue
) -> None:
    pending = {await requested(repository, offset_seconds=i) for i in range(PARALLEL_CLAIMS // 2)}

    jobs = await asyncio.gather(*(queue.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    taken = [job.incident_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(pending)  # cada pedido uma vez, e nenhum perdido


@pytest.mark.usefixtures("clean")
async def test_finish_writes_incident_events_cards_and_usage_together(
    repository: PostgresIncidentRepository,
    queue: PostgresTriageQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    job = await queue.claim(STARTED)
    assert job is not None
    stored = await repository.get_incident(incident_id)
    assert stored is not None
    raised = replace(stored, severity=Severity.P2)
    events = [
        IncidentEvent(incident_id, FINISHED, EventType.TRIAGE_COMPLETED, {}),
        IncidentEvent(incident_id, FINISHED, EventType.SEVERITY_CHANGED, {"para": "P2"}),
    ]
    cards = [
        make_notification(incident_id, NotificationKind.MERGE_SUGGESTED),
        make_notification(incident_id, NotificationKind.SEVERITY_RAISED),
    ]

    await queue.finish(
        job, raised, events=events, notifications=cards, usages=[USAGE], reason=None, at=FINISHED
    )

    assert await repository.get_incident(incident_id) == raised
    assert await repository.events_of(incident_id) == events
    queued = [
        item.notification
        for item in await PostgresNotificationOutbox(database).due(FINISHED, 10_000)
        if item.notification.incident_id == incident_id
    ]
    assert queued == cards
    assert await triage_row(database, incident_id) == (
        "feito",
        T0,
        STARTED,
        FINISHED,
        None,
    )
    async with database.engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.text("SELECT stage, input_tokens FROM llm_usage WHERE incident_id = :id"),
                {"id": incident_id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [(STAGE_TRIAGE, 900)]
    assert await queue.stuck(FINISHED + timedelta(hours=1)) == []


@pytest.mark.usefixtures("clean")
async def test_finish_with_reason_fails_without_touching_the_incident(
    repository: PostgresIncidentRepository,
    queue: PostgresTriageQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    job = await queue.claim(STARTED)
    assert job is not None
    before = await repository.get_incident(incident_id)
    event = IncidentEvent(incident_id, FINISHED, EventType.TRIAGE_COMPLETED, {})

    await queue.finish(
        job, None, events=[event], notifications=[], usages=[], reason="timeout", at=FINISHED
    )

    assert await repository.get_incident(incident_id) == before
    assert await repository.events_of(incident_id) == [event]
    assert await triage_row(database, incident_id) == (
        "falhou",
        T0,
        STARTED,
        FINISHED,
        "timeout",
    )
    assert await counts(database, incident_id) == (0, 0)


@pytest.mark.usefixtures("clean")
async def test_finish_is_all_or_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresTriageQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    job = await queue.claim(STARTED)
    assert job is not None
    unknown = IncidentEvent(uuid4(), FINISHED, EventType.TRIAGE_COMPLETED, {})
    card = make_notification(incident_id, NotificationKind.MERGE_SUGGESTED)

    with pytest.raises(IntegrityError):
        await queue.finish(
            job,
            None,
            events=[unknown],
            notifications=[card],
            usages=[USAGE],
            reason=None,
            at=FINISHED,
        )

    assert (await triage_row(database, incident_id))[0] == "em_andamento"
    assert await counts(database, incident_id) == (0, 0)


@pytest.mark.usefixtures("clean")
async def test_finish_of_a_pending_triage_does_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresTriageQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    job = TriageJob(incident_id, T0, STARTED)

    await queue.finish(
        job, None, events=[], notifications=[], usages=[USAGE], reason=None, at=FINISHED
    )

    assert (await triage_row(database, incident_id))[0] == "pendente"
