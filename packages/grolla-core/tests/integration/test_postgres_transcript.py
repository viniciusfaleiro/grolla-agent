"""Transcrições de call sobre Postgres real: o evento no log, a fila com SKIP LOCKED, o desfecho
numa transação só e o reprocessar.

A fila pega a pendente mais antiga de todo o banco, então este módulo usa um banco próprio
dentro do container da sessão.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresTranscriptStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.investigation import STAGE_TRANSCRIPT
from grolla.application.transcript import TranscriptJob
from grolla.domain.incidents import EventType, IncidentEvent
from grolla.domain.transcript import TRANSCRIPT_ORIGIN, Transcript
from grolla.testing.builders import T0, make_incident
from grolla.testing.investigation import fake_usage
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 8
STARTED = T0 + timedelta(minutes=1)
FINISHED = T0 + timedelta(minutes=2)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"transcript_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


@pytest.fixture(autouse=True)
async def clean(database: PostgresDatabase) -> None:
    async with database.engine.begin() as connection:
        await connection.execute(sa.text("TRUNCATE transcripts, llm_usage"))


@pytest.fixture
def store(database: PostgresDatabase) -> PostgresTranscriptStore:
    return PostgresTranscriptStore(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


async def new_incident(repository: PostgresIncidentRepository) -> UUID:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident.id


async def received(
    store: PostgresTranscriptStore, incident_id: UUID, text: str = "Ana: vamos reverter"
) -> Transcript:
    event = IncidentEvent(
        incident_id, T0, EventType.TRANSCRIPT_RECEIVED, {"chars": len(text)}, "Ana"
    )
    return await store.add(Transcript(0, incident_id, "Ana", text, T0), event)


async def claimed(store: PostgresTranscriptStore, transcript: Transcript) -> TranscriptJob:
    job = await store.claim(STARTED)
    assert job is not None
    assert job.transcript == transcript
    return job


def item(incident_id: UUID) -> IncidentEvent:
    return IncidentEvent(
        incident_id,
        FINISHED,
        EventType.TRANSCRIPT_ITEM,
        {"kind": "decisao", "text": "reverter", "origin": TRANSCRIPT_ORIGIN},
        TRANSCRIPT_ORIGIN,
    )


async def usage_rows(database: PostgresDatabase) -> list[tuple[UUID | None, str]]:
    async with database.engine.connect() as connection:
        result = await connection.execute(sa.text("SELECT incident_id, stage FROM llm_usage"))
        return [(row.incident_id, row.stage) for row in result]


async def test_add_saves_pending_transcript_and_event_with_its_id(
    store: PostgresTranscriptStore, repository: PostgresIncidentRepository
) -> None:
    incident_id = await new_incident(repository)

    saved = await received(store, incident_id)

    assert saved.id > 0
    assert await store.get(saved.id) == saved
    assert await store.get(saved.id + 1_000) is None
    (event,) = await repository.events_of(incident_id)
    assert event.type is EventType.TRANSCRIPT_RECEIVED
    assert event.data == {"chars": len(saved.text), "transcript": saved.id}
    assert event.author == "Ana"


async def test_add_without_incident_saves_nothing(
    store: PostgresTranscriptStore, database: PostgresDatabase
) -> None:
    missing = uuid4()
    with pytest.raises(IntegrityError):
        await received(store, missing)

    assert await store.claim(STARTED) is None
    async with database.engine.connect() as connection:
        events = await connection.execute(
            sa.text("SELECT count(*) FROM incident_events WHERE incident_id = :id"),
            {"id": missing},
        )
        assert events.scalar_one() == 0


async def test_claim_takes_oldest_pending_once_and_stuck_lists_running(
    store: PostgresTranscriptStore, repository: PostgresIncidentRepository
) -> None:
    incident_id = await new_incident(repository)
    first = await received(store, incident_id, "Ana: primeira")
    second = await received(store, incident_id, "Ana: segunda")

    job = await claimed(store, first)
    assert job.started_at == STARTED
    assert await store.stuck(STARTED) == []
    assert await store.stuck(STARTED + timedelta(seconds=1)) == [job]
    await claimed(store, second)
    assert await store.claim(STARTED) is None


async def test_parallel_claims_never_take_the_same_transcript(
    store: PostgresTranscriptStore, repository: PostgresIncidentRepository
) -> None:
    incident_id = await new_incident(repository)
    ids = {(await received(store, incident_id, f"Ana: {n}")).id for n in range(PARALLEL_CLAIMS)}

    jobs = await asyncio.gather(*(store.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    assert {job.transcript.id for job in jobs if job is not None} == ids


async def test_finish_writes_events_and_usage_once(
    store: PostgresTranscriptStore,
    repository: PostgresIncidentRepository,
    database: PostgresDatabase,
) -> None:
    incident_id = await new_incident(repository)
    job = await claimed(store, await received(store, incident_id))
    usage = fake_usage(1_000, STAGE_TRANSCRIPT)

    await store.finish(job, [item(incident_id)], [usage], None, FINISHED)
    await store.finish(job, [item(incident_id)], [usage], None, FINISHED)

    events = await repository.events_of(incident_id)
    assert [e.type for e in events] == [EventType.TRANSCRIPT_RECEIVED, EventType.TRANSCRIPT_ITEM]
    assert events[1].author == TRANSCRIPT_ORIGIN
    assert await usage_rows(database) == [(incident_id, STAGE_TRANSCRIPT)]
    assert await store.stuck(FINISHED) == []
    assert not await store.retry(job.transcript.id, FINISHED)


async def test_failed_transcript_keeps_reason_and_retry_requeues_it(
    store: PostgresTranscriptStore,
    repository: PostgresIncidentRepository,
    database: PostgresDatabase,
) -> None:
    incident_id = await new_incident(repository)
    transcript = await received(store, incident_id)
    assert not await store.retry(transcript.id, STARTED)  # pendente não falhou
    job = await claimed(store, transcript)
    failed = IncidentEvent(
        incident_id, FINISHED, EventType.TRANSCRIPT_FAILED, {"transcript": transcript.id}
    )

    await store.finish(job, [failed], [], "tempo esgotado", FINISHED)

    async with database.engine.connect() as connection:
        row = (
            await connection.execute(
                sa.text("SELECT status, reason, finished_at FROM transcripts WHERE id = :id"),
                {"id": transcript.id},
            )
        ).one()
    assert (row.status, row.reason, row.finished_at) == ("falhou", "tempo esgotado", FINISHED)
    assert await store.claim(STARTED) is None

    assert await store.retry(transcript.id, FINISHED)
    assert not await store.retry(transcript.id, FINISHED)
    again = await claimed(store, transcript)
    await store.finish(again, [item(incident_id)], [], None, FINISHED)
    types = [e.type for e in await repository.events_of(incident_id)]
    assert types[-2:] == [EventType.TRANSCRIPT_FAILED, EventType.TRANSCRIPT_ITEM]


async def test_retried_transcript_goes_to_the_end_of_the_queue(
    store: PostgresTranscriptStore, repository: PostgresIncidentRepository
) -> None:
    incident_id = await new_incident(repository)
    old = await received(store, incident_id, "Ana: antiga")
    await store.finish(await claimed(store, old), [], [], "falhou", FINISHED)
    new = await received(store, incident_id, "Ana: nova")

    assert await store.retry(old.id, FINISHED)

    await claimed(store, new)
    await claimed(store, old)
