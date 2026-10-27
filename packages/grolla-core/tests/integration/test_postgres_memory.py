"""Memória viva sobre Postgres real: registros, versões, busca, fila do aprendizado e estudo.

As filas pegam o pedido mais antigo de todo o banco, então este módulo usa um banco próprio e
limpa as tabelas da memória antes de cada teste.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.learning_queue_contract import LearningQueueContract
from contracts.memory_store_contract import MemoryStoreContract
from contracts.study_queue_contract import StudyQueueContract
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresLearningQueue,
    PostgresMemoryStore,
    PostgresStudyQueue,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_LEARNING, STAGE_STUDY, LlmUsage
from grolla.application.memory import (
    LEARNING_ATTEMPTS,
    LearningFailure,
    LearningQueue,
    ServiceStudy,
    StudyMode,
    StudyQueue,
    StudyStatus,
)
from grolla.application.memory_store import MemoryStore, SubjectSummary
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    EPISODE_LIMIT,
    Confidence,
    Memory,
    MemoryKind,
    MemoryVersion,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.testing.builders import T0, make_incident
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL = 8
STUDY = Source(kind=SourceKind.STUDY, author=AGENT_AUTHOR, ref="https://observabilidade/x")


def usage(stage: str, tokens: int = 100) -> LlmUsage:
    return LlmUsage(
        stage=stage,
        model="modelo-x",
        input_tokens=tokens,
        output_tokens=10,
        cache_read_tokens=5,
        cache_creation_tokens=1,
    )


def make_memory(  # noqa: PLR0913 — cada campo do registro é ajustável no teste
    *,
    subject: str,
    kind: MemoryKind = MemoryKind.DOSSIER,
    subject_kind: SubjectKind = SubjectKind.SERVICE,
    title: str = "Dossiê",
    body: str = "texto",
    confidence: Confidence = Confidence.LEARNED,
    minutes: int = 0,
    incident_id: UUID | None = None,
    active: bool = True,
    consolidated: bool = False,
) -> Memory:
    at = T0 + timedelta(minutes=minutes)
    return Memory(
        id=uuid4(),
        kind=kind,
        subject=subject,
        subject_kind=subject_kind,
        title=title,
        body=body,
        confidence=confidence,
        source=STUDY,
        created_at=at,
        updated_at=at,
        active=active,
        consolidated=consolidated,
        incident_id=incident_id,
    )


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"memoria_{uuid4().hex[:8]}"
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
            sa.text(
                "TRUNCATE memory_chunks, memory_versions, memories, learnings, service_studies,"
                " llm_usage, study_runs"
            )
        )


@pytest.fixture
def store(database: PostgresDatabase) -> PostgresMemoryStore:
    return PostgresMemoryStore(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def learnings(database: PostgresDatabase) -> PostgresLearningQueue:
    return PostgresLearningQueue(database)


@pytest.fixture
def studies(database: PostgresDatabase) -> PostgresStudyQueue:
    return PostgresStudyQueue(database)


class TestPostgresMemoryStoreContract(MemoryStoreContract):
    @pytest.fixture
    def memory(self, database: PostgresDatabase) -> MemoryStore:
        return PostgresMemoryStore(database)


class TestPostgresLearningQueueContract(LearningQueueContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def queue(self, database: PostgresDatabase) -> LearningQueue:
        return PostgresLearningQueue(database)


class TestPostgresStudyQueueContract(StudyQueueContract):
    @pytest.fixture
    def studies(self, database: PostgresDatabase) -> StudyQueue:
        return PostgresStudyQueue(database)


async def saved(store: PostgresMemoryStore, *memories: Memory) -> None:
    await store.save([(memory, None) for memory in memories])


async def incident_id(repository: PostgresIncidentRepository) -> UUID:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident.id


async def usage_rows(database: PostgresDatabase) -> list[sa.Row[tuple[object, ...]]]:
    async with database.engine.connect() as connection:
        result = await connection.execute(
            sa.text("SELECT incident_id, study_run_id, stage FROM llm_usage ORDER BY id")
        )
        return list(result)


# ---- MemoryStore ----


async def test_save_round_trips_and_updates_by_id(store: PostgresMemoryStore) -> None:
    memory = make_memory(subject="pedidos-api", body="Serviço de pedidos.")
    await saved(store, memory)
    assert await store.get(memory.id) == memory

    edited = replace(memory, body="Novo texto", confidence=Confidence.CONFIRMED, active=False)
    version = MemoryVersion(
        memory_id=memory.id, at=T0, author="ana", reason="edição", body=memory.body, active=True
    )
    await store.save([(edited, version)])

    assert await store.get(memory.id) == edited
    assert await store.versions(memory.id) == [version]


async def test_get_unknown_is_none(store: PostgresMemoryStore) -> None:
    assert await store.get(uuid4()) is None


async def test_save_is_one_transaction(store: PostgresMemoryStore) -> None:
    first = make_memory(subject="pedidos-api")
    duplicated = make_memory(subject="pedidos-api")  # segundo dossiê do mesmo assunto

    with pytest.raises(IntegrityError):
        await saved(store, first, duplicated)

    assert await store.get(first.id) is None


async def test_versions_newest_first(store: PostgresMemoryStore) -> None:
    memory = make_memory(subject="pedidos-api")
    older = MemoryVersion(memory.id, T0, "ana", "um", "a", True)
    newer = MemoryVersion(memory.id, T0 + timedelta(hours=1), "bia", "dois", "b", False)
    await store.save([(memory, older)])
    await store.save([(memory, newer)])

    assert await store.versions(memory.id) == [newer, older]


async def test_dossier_includes_forgotten(store: PostgresMemoryStore) -> None:
    forgotten = make_memory(subject="pedidos-api", active=False)
    await saved(store, forgotten)

    assert await store.dossier("pedidos-api") == forgotten
    assert await store.dossier("outro") is None


async def test_episode_of_and_one_per_incident_and_subject(
    store: PostgresMemoryStore, repository: PostgresIncidentRepository
) -> None:
    incident = await incident_id(repository)
    episode = make_memory(
        subject="pedidos-api", kind=MemoryKind.EPISODE, title="Erro 500", incident_id=incident
    )
    other_subject = replace(episode, id=uuid4(), subject="gateway")
    await saved(store, episode, other_subject)

    assert await store.episode_of(incident, "pedidos-api") == episode
    assert await store.episode_of(uuid4(), "pedidos-api") is None
    with pytest.raises(IntegrityError):
        await saved(store, replace(episode, id=uuid4()))


async def test_all_active_and_of_subject(store: PostgresMemoryStore) -> None:
    dossier = make_memory(subject="pedidos-api")
    note = make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, minutes=1)
    episode = make_memory(subject="pedidos-api", kind=MemoryKind.EPISODE, minutes=2)
    forgotten = make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, active=False)
    other = make_memory(subject="gateway")
    await saved(store, note, forgotten, episode, dossier, other)

    assert {m.id for m in await store.all_active()} == {
        dossier.id,
        note.id,
        episode.id,
        other.id,
    }
    assert [m.id for m in await store.of_subject("pedidos-api")] == [
        dossier.id,
        episode.id,
        note.id,
        forgotten.id,
    ]


async def test_search_matches_portuguese_stems_confirmed_first(
    store: PostgresMemoryStore,
) -> None:
    learned = make_memory(
        subject="pedidos-api",
        kind=MemoryKind.NOTE,
        body="O banco de pedidos fica lento às segundas.",
        minutes=5,
    )
    confirmed = make_memory(
        subject="gateway",
        kind=MemoryKind.NOTE,
        body="Lentidão no banco quando o lote noturno roda.",
        confidence=Confidence.CONFIRMED,
    )
    unrelated = make_memory(subject="gateway", body="Sem relação.")
    forgotten = make_memory(
        subject="gateway", kind=MemoryKind.NOTE, body="banco antigo", active=False
    )
    await saved(store, learned, confirmed, unrelated, forgotten)

    assert [m.id for m in await store.search(None, "bancos", 10)] == [confirmed.id, learned.id]
    assert [m.id for m in await store.search("pedidos-api", "banco", 10)] == [learned.id]
    assert [m.id for m in await store.search(None, "banco", 1)] == [confirmed.id]
    assert await store.search(None, "", 10) == []


async def test_context_for_brings_dossiers_notes_and_cited_integrations(
    store: PostgresMemoryStore,
) -> None:
    environment = make_memory(subject=ENVIRONMENT, subject_kind=SubjectKind.ENVIRONMENT)
    service = make_memory(subject="pedidos-api", body="Depende do Redis-Sessoes e do Postgres.")
    redis = make_memory(subject="redis-sessoes", subject_kind=SubjectKind.INTEGRATION)
    uncited = make_memory(subject="kafka", subject_kind=SubjectKind.INTEGRATION)
    other_service = make_memory(subject="gateway", body="Usa kafka.")
    note = make_memory(subject="redis-sessoes", kind=MemoryKind.NOTE, body="TTL curto")
    consolidated = make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, consolidated=True)
    env_note = make_memory(subject=ENVIRONMENT, kind=MemoryKind.NOTE, minutes=1)
    other_note = make_memory(subject="gateway", kind=MemoryKind.NOTE)
    forgotten = make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, active=False)
    await saved(
        store,
        redis,
        uncited,
        other_service,
        service,
        environment,
        note,
        consolidated,
        env_note,
        other_note,
        forgotten,
    )

    context = await store.context_for(["pedidos-api"], [], "")

    assert [m.id for m in context.dossiers] == [environment.id, service.id, redis.id]
    assert [m.id for m in context.notes] == [note.id, env_note.id]
    assert context.episodes == ()


async def test_context_for_ignores_forgotten_dossiers(store: PostgresMemoryStore) -> None:
    await saved(store, make_memory(subject="pedidos-api", active=False))

    assert (await store.context_for(["pedidos-api"], ["Erro alto"], "erro")).empty


async def test_context_for_ranks_episodes(store: PostgresMemoryStore) -> None:
    def episode(subject: str, body: str, minutes: int, active: bool = True) -> Memory:
        return make_memory(
            subject=subject,
            kind=MemoryKind.EPISODE,
            title=f"Episódio {minutes}",
            body=body,
            minutes=minutes,
            active=active,
        )

    same_rule = episode("pedidos-api", "Disparou Latência P99 alta no checkout.", 1)
    same_service_text = episode("pedidos-api", "Timeout no banco de dados de pedidos.", 2)
    same_service_recent = episode("pedidos-api", "Sem relação.", 3)
    other_service_text = episode("gateway", "Timeout no banco de dados.", 4)
    other_service = episode("gateway", "Sem relação.", 5)
    forgotten = episode("pedidos-api", "Latência P99 alta de novo.", 6, active=False)
    await saved(
        store,
        same_rule,
        same_service_text,
        same_service_recent,
        other_service_text,
        other_service,
        forgotten,
    )

    context = await store.context_for(["pedidos-api"], ["latência p99 alta"], "timeout banco")

    assert [m.id for m in context.episodes] == [
        same_rule.id,
        same_service_text.id,
        same_service_recent.id,
        other_service_text.id,
    ]
    without_text = await store.context_for(["pedidos-api"], [], "")
    assert [m.id for m in without_text.episodes] == [
        same_service_recent.id,
        same_service_text.id,
        same_rule.id,
    ]


async def test_context_for_limits_episodes(store: PostgresMemoryStore) -> None:
    await saved(
        store,
        *(
            make_memory(subject="pedidos-api", kind=MemoryKind.EPISODE, minutes=i)
            for i in range(EPISODE_LIMIT + 2)
        ),
    )

    assert len((await store.context_for(["pedidos-api"], [], "")).episodes) == EPISODE_LIMIT


async def test_subjects_environment_first_with_study_date(
    store: PostgresMemoryStore, studies: PostgresStudyQueue
) -> None:
    await saved(
        store,
        make_memory(subject="redis", subject_kind=SubjectKind.INTEGRATION, minutes=3),
        make_memory(subject="pedidos-api", minutes=1),
        make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, minutes=2),
        make_memory(subject="pedidos-api", kind=MemoryKind.NOTE, minutes=9, active=False),
        make_memory(subject=ENVIRONMENT, subject_kind=SubjectKind.ENVIRONMENT),
        make_memory(subject="gateway"),
    )
    assert await studies.request(StudyMode.INITIAL, "sistema", T0)
    run = await studies.claim(T0)
    assert run is not None
    studied_at = T0 + timedelta(hours=1)
    await studies.service_done(run, ServiceStudy("pedidos-api", studied_at, True, None), [])

    assert await store.subjects() == [
        SubjectSummary(ENVIRONMENT, SubjectKind.ENVIRONMENT, 1, T0, None),
        SubjectSummary("gateway", SubjectKind.SERVICE, 1, T0, None),
        SubjectSummary(
            "pedidos-api", SubjectKind.SERVICE, 2, T0 + timedelta(minutes=9), studied_at
        ),
        SubjectSummary("redis", SubjectKind.INTEGRATION, 1, T0 + timedelta(minutes=3), None),
    ]


# ---- Aprendizado ----


async def requested_learning(
    repository: PostgresIncidentRepository, offset_seconds: int = 0
) -> UUID:
    incident = await incident_id(repository)
    async with repository.transaction() as uow:
        await uow.request_learning(incident, T0 + timedelta(seconds=offset_seconds))
    return incident


async def test_claim_takes_the_oldest_and_counts_the_attempt(
    repository: PostgresIncidentRepository, learnings: PostgresLearningQueue
) -> None:
    newer = await requested_learning(repository, 10)
    older = await requested_learning(repository, 0)
    now = T0 + timedelta(minutes=1)

    first = await learnings.claim(now)
    second = await learnings.claim(now)

    assert first is not None
    assert second is not None
    assert (first.incident_id, first.attempts, first.started_at) == (older, 1, now)
    assert second.incident_id == newer
    assert await learnings.claim(now) is None


async def test_parallel_claims_never_take_the_same_learning(
    repository: PostgresIncidentRepository, learnings: PostgresLearningQueue
) -> None:
    for offset in range(PARALLEL):
        await requested_learning(repository, offset)

    jobs = await asyncio.gather(*(learnings.claim(T0) for _ in range(PARALLEL)))

    assert len({job.incident_id for job in jobs if job is not None}) == PARALLEL


async def test_failed_learning_retries_then_fails_with_the_incident_title(
    database: PostgresDatabase,
    repository: PostgresIncidentRepository,
    learnings: PostgresLearningQueue,
) -> None:
    incident = await requested_learning(repository)
    failures: list[LearningFailure] = []
    for attempt in range(1, LEARNING_ATTEMPTS + 1):
        job = await learnings.claim(T0)
        assert job is not None
        assert job.attempts == attempt
        assert await learnings.failed() == failures
        await learnings.finish(job, False, "falha do modelo", [usage(STAGE_LEARNING)], T0)

    assert await learnings.claim(T0) is None
    assert await learnings.failed() == [
        LearningFailure(incident, "Taxa de erro alta", "falha do modelo", T0)
    ]
    assert [tuple(row) for row in await usage_rows(database)] == [
        (incident, None, STAGE_LEARNING)
    ] * LEARNING_ATTEMPTS


async def test_finish_only_when_running(
    database: PostgresDatabase,
    repository: PostgresIncidentRepository,
    learnings: PostgresLearningQueue,
) -> None:
    await requested_learning(repository)
    job = await learnings.claim(T0)
    assert job is not None
    await learnings.finish(job, True, None, [usage(STAGE_LEARNING)], T0)

    await learnings.finish(job, False, "atrasado", [usage(STAGE_LEARNING)], T0)

    assert await learnings.failed() == []
    assert await learnings.claim(T0) is None
    assert len(await usage_rows(database)) == 1


async def test_stuck_learnings(
    repository: PostgresIncidentRepository, learnings: PostgresLearningQueue
) -> None:
    await requested_learning(repository)
    job = await learnings.claim(T0)

    assert await learnings.stuck(T0 + timedelta(minutes=1)) == [job]
    assert await learnings.stuck(T0) == []


async def test_new_request_resets_to_pending_with_no_attempts(
    repository: PostgresIncidentRepository, learnings: PostgresLearningQueue
) -> None:
    incident = await requested_learning(repository)
    for _ in range(LEARNING_ATTEMPTS):
        job = await learnings.claim(T0)
        assert job is not None
        await learnings.finish(job, False, "falha", [], T0)
    assert await learnings.failed() != []

    async with repository.transaction() as uow:
        await uow.request_learning(incident, T0 + timedelta(hours=1))

    assert await learnings.failed() == []
    job = await learnings.claim(T0 + timedelta(hours=1))
    assert job is not None
    assert (job.incident_id, job.attempts) == (incident, 1)


# ---- Estudo ----


async def test_request_does_not_duplicate_open_runs(studies: PostgresStudyQueue) -> None:
    assert await studies.request(StudyMode.INITIAL, "sistema", T0)
    assert not await studies.request(StudyMode.DAILY, "sistema", T0 + timedelta(minutes=1))
    run = await studies.claim(T0)
    assert run is not None
    assert not await studies.request(StudyMode.FULL, "ana", T0 + timedelta(minutes=2))

    last = await studies.last()
    assert last is not None
    assert (last.id, last.mode, last.status) == (run.id, StudyMode.INITIAL, StudyStatus.RUNNING)


async def test_parallel_requests_create_one_run(studies: PostgresStudyQueue) -> None:
    results = await asyncio.gather(
        *(studies.request(StudyMode.DAILY, "sistema", T0) for _ in range(PARALLEL))
    )

    assert results.count(True) == 1


async def test_relearn_replaces_the_pending_run(studies: PostgresStudyQueue) -> None:
    assert await studies.request(StudyMode.DAILY, "sistema", T0)
    later = T0 + timedelta(minutes=1)

    assert await studies.request(StudyMode.RELEARN, "ana", later)

    run = await studies.claim(later)
    assert run is not None
    assert (run.mode, run.author, run.requested_at) == (StudyMode.RELEARN, "ana", later)
    assert await studies.claim(later) is None


async def test_claim_stuck_finish_and_tokens(
    database: PostgresDatabase, studies: PostgresStudyQueue
) -> None:
    assert await studies.claim(T0) is None
    assert await studies.last() is None
    await studies.request(StudyMode.INITIAL, "sistema", T0)
    started = T0 + timedelta(seconds=5)
    run = await studies.claim(started)
    assert run is not None
    assert (run.status, run.started_at, run.tokens) == (StudyStatus.RUNNING, started, 0)
    assert await studies.stuck(started + timedelta(minutes=1)) == [run]

    await studies.service_done(
        run,
        ServiceStudy("pedidos-api", started, True, None),
        [usage(STAGE_STUDY, 1000), usage(STAGE_STUDY, 500)],
    )
    finished = T0 + timedelta(minutes=10)
    await studies.finish(run, StudyStatus.DONE, None, finished)
    await studies.finish(run, StudyStatus.FAILED, "atrasado", finished)  # já encerrada

    last = await studies.last()
    assert last is not None
    assert (last.status, last.finished_at, last.reason) == (StudyStatus.DONE, finished, None)
    assert last.tokens == 1016 + 516
    assert await studies.stuck(finished) == []
    assert [tuple(row) for row in await usage_rows(database)] == [
        (None, run.id, STAGE_STUDY),
        (None, run.id, STAGE_STUDY),
    ]


async def test_service_order_and_services(studies: PostgresStudyQueue) -> None:
    await studies.request(StudyMode.DAILY, "sistema", T0)
    run = await studies.claim(T0)
    assert run is not None
    old = T0 - timedelta(days=3)
    recent = T0 - timedelta(days=1)
    await studies.service_done(run, ServiceStudy("recente", recent, True, None), [])
    await studies.service_done(run, ServiceStudy("antigo", old, True, None), [])
    await studies.service_done(run, ServiceStudy("sem-orcamento", None, False, "sem orçamento"), [])
    await studies.service_done(run, ServiceStudy("falhou", recent, True, None), [])
    await studies.service_done(run, ServiceStudy("falhou", None, False, "timeout"), [])

    services = ["recente", "antigo", "novo", "falhou", "sem-orcamento"]
    expected = ["novo", "falhou", "sem-orcamento", "antigo", "recente"]
    assert await studies.service_order(services, StudyMode.DAILY) == expected
    assert await studies.service_order(services, StudyMode.RELEARN) == expected
    assert await studies.services() == [
        ServiceStudy("antigo", old, True, None),
        ServiceStudy("falhou", recent, False, "timeout"),  # mantém o último estudo bem feito
        ServiceStudy("recente", recent, True, None),
        ServiceStudy("sem-orcamento", None, False, "sem orçamento"),
    ]
