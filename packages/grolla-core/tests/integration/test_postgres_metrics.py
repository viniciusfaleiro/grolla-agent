"""Fatos das métricas do agente sobre Postgres real.

A leitura vê todos os incidentes, post mortems e eventos do banco (e o log não aceita TRUNCATE),
então cada teste usa um banco próprio dentro do container da sessão.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresMetricsReader,
    PostgresPostMortemStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState
from grolla.domain.metrics import MetricsFacts
from grolla.domain.post_mortem import AgentEvaluation, PostMortemStatus
from grolla.testing.builders import T0, make_incident
from grolla.testing.post_mortem import make_post_mortem
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

SINCE = T0 + timedelta(days=1)
BEFORE = SINCE - timedelta(minutes=1)
AFTER = SINCE + timedelta(hours=1)
LATER = SINCE + timedelta(hours=2)


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


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def reader(database: PostgresDatabase) -> PostgresMetricsReader:
    return PostgresMetricsReader(database)


async def saved(
    repository: PostgresIncidentRepository,
    *,
    state: IncidentState = IncidentState.RESOLVED,
    resolved_at: datetime | None = AFTER,
    answers: tuple[tuple[datetime, bool], ...] = (),
) -> Incident:
    incident = replace(make_incident(state=state), resolved_at=resolved_at)
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.append(
            [
                IncidentEvent(
                    incident_id=incident.id,
                    at=at,
                    type=EventType.SEEN_BEFORE_ANSWERED,
                    data={"past": str(uuid4()), "same": same},
                    author="ana",
                )
                for at, same in answers
            ]
        )
    return incident


async def test_empty_database_has_no_facts(reader: PostgresMetricsReader) -> None:
    assert await reader.facts(SINCE) == MetricsFacts()


async def test_resolved_are_the_resolved_incidents_since(
    repository: PostgresIncidentRepository, reader: PostgresMetricsReader
) -> None:
    await saved(repository, resolved_at=LATER)
    await saved(repository, resolved_at=SINCE)
    await saved(repository, resolved_at=BEFORE)
    await saved(repository, state=IncidentState.HANDLING, resolved_at=None)

    assert (await reader.facts(SINCE)).resolved == (SINCE, LATER)


async def test_approved_are_approved_or_closed_post_mortems_since_with_the_evaluation(
    repository: PostgresIncidentRepository,
    reader: PostgresMetricsReader,
    database: PostgresDatabase,
) -> None:
    store = PostgresPostMortemStore(database)
    evaluation = AgentEvaluation(
        main_hypothesis_correct=True, minutes_to_right=12, memory_helped=False, author="ana", at=T0
    )
    rows = (
        (PostMortemStatus.APPROVED, LATER, evaluation),
        (PostMortemStatus.CLOSED, AFTER, AgentEvaluation()),
        (PostMortemStatus.APPROVED, BEFORE, evaluation),
        (PostMortemStatus.REVIEW, AFTER, evaluation),
    )
    for status, at, value in rows:
        incident = await saved(repository)
        pm = make_post_mortem(incident.id, status=status)
        await store.save(replace(pm, status_at=at, status_by="ana", evaluation=value), [])

    assert (await reader.facts(SINCE)).approved == ((AFTER, AgentEvaluation()), (LATER, evaluation))


async def test_answers_keep_only_the_last_one_of_each_incident(
    repository: PostgresIncidentRepository, reader: PostgresMetricsReader
) -> None:
    await saved(repository, answers=((AFTER, True), (LATER, False)))
    await saved(repository, answers=((AFTER, True),))
    await saved(repository, answers=((BEFORE, True),))
    await saved(repository, answers=((AFTER, False), (BEFORE, True)))  # a última do log é anterior

    assert (await reader.facts(SINCE)).answers == ((AFTER, True), (LATER, False))
