"""Post mortem sobre Postgres real: documento, versões, lista com filtros e a fila.

A fila pega o pedido pendente mais antigo de todo o banco e a lista vê todos os post mortems,
então este módulo usa um banco próprio dentro do container da sessão.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresPostMortemStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.investigation import STAGE_POST_MORTEM, LlmUsage
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    ActionItem,
    ActionKind,
    ActionStatus,
    PostMortem,
    PostMortemStatus,
    Section,
    SectionKey,
    SectionVersion,
)
from grolla.testing.builders import T0, make_incident
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 8
STARTED = T0 + timedelta(minutes=5)
FINISHED = T0 + timedelta(minutes=6)
TODAY = date(2026, 1, 10)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"post_mortem_{uuid4().hex[:8]}"
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
        await connection.execute(
            sa.text("TRUNCATE post_mortems, post_mortem_versions, post_mortem_jobs, llm_usage")
        )


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def store(database: PostgresDatabase) -> PostgresPostMortemStore:
    return PostgresPostMortemStore(database)


async def saved(
    repository: PostgresIncidentRepository,
    *,
    severity: Severity = Severity.P3,
    services: tuple[str, ...] | None = None,
    detected_at: datetime = T0,
) -> Incident:
    incident = make_incident(
        state=IncidentState.RESOLVED, severity=severity, services=services, detected_at=detected_at
    )
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident


def post_mortem(
    incident: Incident,
    *,
    status: PostMortemStatus = PostMortemStatus.DRAFT,
    actions: tuple[ActionItem, ...] = (),
    updated_at: datetime = T0,
) -> PostMortem:
    return PostMortem(
        incident_id=incident.id,
        status=status,
        sections=tuple(
            Section(key=key, body=f"texto de {key.value}", author=AGENT_AUTHOR, updated_at=T0)
            for key in SectionKey
        ),
        actions=actions,
        created_at=T0,
        updated_at=updated_at,
    )


def action(
    *, due: date | None = None, status: ActionStatus = ActionStatus.OPEN, owner: str | None = None
) -> ActionItem:
    return ActionItem(
        id=uuid4(),
        description="Alerta de saturação do pool",
        kind=ActionKind.DETECT,
        owner=owner,
        due=due,
        status=status,
    )


def version(incident: Incident, key: SectionKey, minutes: int, body: str) -> SectionVersion:
    return SectionVersion(
        incident_id=incident.id,
        key=key,
        at=T0 + timedelta(minutes=minutes),
        author="ana",
        reason="edição",
        body=body,
    )


def usage(tokens: int) -> LlmUsage:
    return LlmUsage(
        stage=STAGE_POST_MORTEM,
        model="modelo-x",
        input_tokens=tokens,
        output_tokens=10,
        cache_read_tokens=0,
        cache_creation_tokens=0,
    )


async def draft_job(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore, incident: Incident
) -> PostMortemJob:
    async with repository.transaction() as uow:
        await uow.request_post_mortem(incident.id, T0)
    job = await store.claim(STARTED)
    assert job is not None
    return job


async def jobs(database: PostgresDatabase) -> list[tuple[str, str | None]]:
    async with database.engine.connect() as connection:
        result = await connection.execute(
            sa.text("SELECT status, reason FROM post_mortem_jobs ORDER BY id")
        )
        return [(row.status, row.reason) for row in result]


async def test_get_without_post_mortem_is_none(store: PostgresPostMortemStore) -> None:
    assert await store.get(uuid4()) is None


async def test_save_round_trips_sections_actions_and_status(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    pm = replace(
        post_mortem(
            incident,
            status=PostMortemStatus.REVIEW,
            actions=(action(due=date(2026, 2, 1), owner="ana"), action()),
        ),
        status_by="ana",
        status_at=STARTED,
    )

    await store.save(pm, [])

    assert await store.get(incident.id) == pm


async def test_save_again_replaces_the_document_and_keeps_created_at(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    pm = post_mortem(incident)
    await store.save(pm, [])
    changed = replace(pm, status=PostMortemStatus.REVIEW, actions=(action(),), updated_at=FINISHED)

    await store.save(changed, [version(incident, SectionKey.SUMMARY, 6, "antes")])

    assert await store.get(incident.id) == changed


async def test_versions_are_per_section_newest_first(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    first = version(incident, SectionKey.SUMMARY, 1, "primeiro")
    second = version(incident, SectionKey.SUMMARY, 2, "segundo")
    other = version(incident, SectionKey.IMPACT, 3, "impacto")

    await store.save(post_mortem(incident), [first, other])
    await store.save(post_mortem(incident), [second])

    assert await store.versions(incident.id, SectionKey.SUMMARY) == [second, first]
    assert await store.versions(incident.id, SectionKey.IMPACT) == [other]
    assert await store.versions(incident.id, SectionKey.LESSONS) == []


async def test_summaries_are_newest_first_with_overdue_count(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    old = await saved(repository, detected_at=T0)
    new = await saved(repository, detected_at=T0 + timedelta(days=2))
    late = action(due=TODAY - timedelta(days=1))
    actions = (
        late,
        action(due=TODAY),  # vence hoje: ainda não está vencido
        action(due=TODAY - timedelta(days=3), status=ActionStatus.DONE),
        action(),
    )
    await store.save(post_mortem(old, actions=actions), [])
    await store.save(post_mortem(new, updated_at=FINISHED), [])

    summaries = await store.summaries(PostMortemFilters(), TODAY)

    assert [(s.incident, s.status, s.updated_at, s.overdue) for s in summaries] == [
        (new, PostMortemStatus.DRAFT, FINISHED, 0),
        (old, PostMortemStatus.DRAFT, T0, 1),
    ]
    assert [s.incident.id for s in await store.summaries(PostMortemFilters(limit=1), TODAY)] == [
        new.id
    ]


async def test_summaries_filter_by_status_severity_service_and_period(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    approved = await saved(
        repository, severity=Severity.P1, services=("capture-api", "auth-api"), detected_at=T0
    )
    draft = await saved(repository, severity=Severity.P3, detected_at=T0 + timedelta(days=3))
    await store.save(post_mortem(approved, status=PostMortemStatus.APPROVED), [])
    await store.save(post_mortem(draft), [])

    async def ids(filters: PostMortemFilters) -> list[object]:
        return [s.incident.id for s in await store.summaries(filters, TODAY)]

    assert await ids(PostMortemFilters(status=PostMortemStatus.APPROVED)) == [approved.id]
    assert await ids(PostMortemFilters(severity=Severity.P3)) == [draft.id]
    assert await ids(PostMortemFilters(service="auth-api")) == [approved.id]
    assert await ids(PostMortemFilters(service="nenhum")) == []
    assert await ids(PostMortemFilters(since=date(2026, 1, 4))) == [draft.id]
    assert await ids(PostMortemFilters(until=date(2026, 1, 1))) == [approved.id]
    assert await ids(PostMortemFilters(since=date(2026, 1, 1), until=date(2026, 1, 4))) == [
        draft.id,
        approved.id,
    ]


async def test_claim_without_pending_is_none(store: PostgresPostMortemStore) -> None:
    assert await store.claim(STARTED) is None


async def test_draft_request_is_idempotent_and_claimed_once(
    repository: PostgresIncidentRepository,
    store: PostgresPostMortemStore,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    async with repository.transaction() as uow:
        await uow.request_post_mortem(incident.id, T0)
        await uow.request_post_mortem(incident.id, T0 + timedelta(minutes=1))

    job = await store.claim(STARTED)

    assert job is not None
    assert job == PostMortemJob(
        id=job.id,
        incident_id=incident.id,
        key=None,
        instruction=None,
        author=None,
        started_at=STARTED,
    )
    assert await store.claim(STARTED) is None
    assert await jobs(database) == [("em_andamento", None)]


async def test_regeneration_is_claimed_in_request_order(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    await store.request_regeneration(
        incident.id, SectionKey.IMPACT, instruction=None, author="bia", at=T0 + timedelta(minutes=1)
    )
    await store.request_regeneration(
        incident.id, SectionKey.SUMMARY, instruction="mais curto", author="ana", at=T0
    )

    first = await store.claim(STARTED)
    second = await store.claim(STARTED)

    assert first is not None
    assert (first.key, first.instruction, first.author) == (SectionKey.SUMMARY, "mais curto", "ana")
    assert second is not None
    assert (second.key, second.instruction, second.author) == (SectionKey.IMPACT, None, "bia")


async def test_parallel_claims_take_different_jobs(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    for _ in range(PARALLEL_CLAIMS):
        await store.request_regeneration(
            incident.id, SectionKey.SUMMARY, instruction=None, author="ana", at=T0
        )

    claimed = await asyncio.gather(*(store.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    assert len({job.id for job in claimed if job is not None}) == PARALLEL_CLAIMS


async def test_stuck_lists_running_jobs_started_before(
    repository: PostgresIncidentRepository, store: PostgresPostMortemStore
) -> None:
    incident = await saved(repository)
    job = await draft_job(repository, store, incident)

    assert await store.stuck(STARTED) == []
    assert await store.stuck(STARTED + timedelta(seconds=1)) == [job]


async def test_finish_saves_document_versions_and_usage_and_closes_the_job(
    repository: PostgresIncidentRepository,
    store: PostgresPostMortemStore,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    job = await draft_job(repository, store, incident)
    pm = post_mortem(incident)
    old = version(incident, SectionKey.SUMMARY, 6, "antes")

    await store.finish(job, pm, versions=[old], usages=[usage(100)], reason=None, at=FINISHED)

    assert await store.get(incident.id) == pm
    assert await store.versions(incident.id, SectionKey.SUMMARY) == [old]
    assert await jobs(database) == [("feito", None)]
    assert await store.stuck(FINISHED + timedelta(hours=1)) == []
    async with database.engine.connect() as connection:
        result = await connection.execute(
            sa.text("SELECT incident_id, stage, input_tokens FROM llm_usage")
        )
        assert [tuple(row) for row in result] == [(incident.id, STAGE_POST_MORTEM, 100)]


async def test_finish_failed_without_document_records_the_reason(
    repository: PostgresIncidentRepository,
    store: PostgresPostMortemStore,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    job = await draft_job(repository, store, incident)

    await store.finish(job, None, versions=[], usages=[], reason="interrompido", at=FINISHED)

    assert await store.get(incident.id) is None
    assert await jobs(database) == [("falho", "interrompido")]


async def test_finish_of_a_closed_job_changes_nothing(
    repository: PostgresIncidentRepository,
    store: PostgresPostMortemStore,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    job = await draft_job(repository, store, incident)
    await store.finish(job, None, versions=[], usages=[], reason="interrompido", at=FINISHED)

    await store.finish(
        job, post_mortem(incident), versions=[], usages=[usage(5)], reason=None, at=FINISHED
    )

    assert await store.get(incident.id) is None
    assert await jobs(database) == [("falho", "interrompido")]
