"""Transcrições de call sobre Postgres real: a suíte de contrato e o que só o banco mostra (a
chave do incidente, SKIP LOCKED, o uso em `llm_usage`, o motivo gravado e a posição na fila
depois de reprocessar).

A fila pega a pendente mais antiga de todo o banco, então este módulo usa um banco próprio
dentro do container da sessão.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.transcript_store_contract import TranscriptStoreContract
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresTranscriptStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_TRANSCRIPT
from grolla.application.transcript import TranscriptJob, TranscriptStore
from grolla.domain.incidents import EventType, IncidentEvent
from grolla.domain.transcript import Transcript
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


class TestPostgresTranscriptStoreContract(TranscriptStoreContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def store(self, database: PostgresDatabase) -> TranscriptStore:
        return PostgresTranscriptStore(database)


@pytest.fixture
def store(database: PostgresDatabase) -> PostgresTranscriptStore:
    return PostgresTranscriptStore(database)


async def received(database: PostgresDatabase, incident_id: UUID | None = None) -> Transcript:
    """Transcrição pendente de um incidente novo (ou do incidente dado)."""
    if incident_id is None:
        incident = make_incident()
        async with PostgresIncidentRepository(database).transaction() as uow:
            await uow.save(incident)
        incident_id = incident.id
    event = IncidentEvent(incident_id, T0, EventType.TRANSCRIPT_RECEIVED, {"chars": 5}, "Ana")
    return await PostgresTranscriptStore(database).add(
        Transcript(0, incident_id, "Ana", "Ana: oi", T0), event
    )


async def claimed(store: PostgresTranscriptStore, transcript: Transcript) -> TranscriptJob:
    job = await store.claim(STARTED)
    assert job is not None
    assert job.transcript == transcript
    return job


async def test_add_without_incident_saves_nothing(
    store: PostgresTranscriptStore, database: PostgresDatabase
) -> None:
    missing = uuid4()
    with pytest.raises(IntegrityError):
        await received(database, missing)

    assert await store.claim(STARTED) is None
    async with database.engine.connect() as connection:
        events = await connection.execute(
            sa.text("SELECT count(*) FROM incident_events WHERE incident_id = :id"),
            {"id": missing},
        )
        assert events.scalar_one() == 0


async def test_parallel_claims_never_take_the_same_transcript(
    store: PostgresTranscriptStore, database: PostgresDatabase
) -> None:
    ids = {(await received(database)).id for _ in range(PARALLEL_CLAIMS)}

    jobs = await asyncio.gather(*(store.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    assert {job.transcript.id for job in jobs if job is not None} == ids


async def test_finish_records_usage_of_the_incident_and_the_reason(
    store: PostgresTranscriptStore, database: PostgresDatabase
) -> None:
    transcript = await received(database)
    job = await claimed(store, transcript)

    await store.finish(job, [], [fake_usage(1_000, STAGE_TRANSCRIPT)], "tempo esgotado", FINISHED)

    async with database.engine.connect() as connection:
        usage = (
            await connection.execute(sa.text("SELECT incident_id, stage FROM llm_usage"))
        ).all()
        row = (
            await connection.execute(
                sa.text("SELECT status, reason, finished_at FROM transcripts WHERE id = :id"),
                {"id": transcript.id},
            )
        ).one()
    assert [tuple(u) for u in usage] == [(transcript.incident_id, STAGE_TRANSCRIPT)]
    assert (row.status, row.reason, row.finished_at) == ("falhou", "tempo esgotado", FINISHED)


async def test_retried_transcript_goes_to_the_end_of_the_queue(
    store: PostgresTranscriptStore, database: PostgresDatabase
) -> None:
    old = await received(database)
    await store.finish(await claimed(store, old), [], [], "falhou", FINISHED)
    new = await received(database)

    assert await store.retry(old.id, FINISHED)

    await claimed(store, new)
    await claimed(store, old)
