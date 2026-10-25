"""Fila de análises, projeção da análise e `llm_usage` sobre Postgres real.

A fila pega o pedido pendente mais antigo de todo o banco, então este módulo usa um banco
próprio dentro do container da sessão: pedidos deixados por outros testes não interferem.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.analysis_queue_contract import AnalysisQueueContract
from grolla.adapters.postgres import (
    PostgresAnalysisQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.analysis import AnalysisQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import LlmUsage
from grolla.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
)
from grolla.domain.incidents import EventType, IncidentEvent, NotificationKind
from grolla.testing.builders import T0, make_incident, make_notification
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(minutes=2)
USAGE = LlmUsage(
    stage="investigation",
    model="modelo-x",
    input_tokens=1200,
    output_tokens=300,
    cache_read_tokens=800,
    cache_creation_tokens=100,
)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"analises_{uuid4().hex[:8]}"
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
        await connection.execute(sa.text("DELETE FROM analyses"))


class TestPostgresAnalysisQueueContract(AnalysisQueueContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def queue(self, database: PostgresDatabase) -> AnalysisQueue:
        return PostgresAnalysisQueue(database)

    @pytest.fixture
    def outbox(self, database: PostgresDatabase) -> NotificationOutbox:
        return PostgresNotificationOutbox(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def queue(database: PostgresDatabase) -> PostgresAnalysisQueue:
    return PostgresAnalysisQueue(database)


async def requested(repository: PostgresIncidentRepository, offset_seconds: int = 0) -> UUID:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_analysis(incident.id, T0 + timedelta(seconds=offset_seconds))
    return incident.id


def completed(incident_id: UUID) -> Analysis:
    return Analysis(
        incident_id=incident_id,
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=STARTED,
        finished_at=FINISHED,
        summary="Erro subiu depois do deploy 1.4.2.",
        evaluations=(
            Evaluation(
                tool="query_metric",
                subject="taxa_de_erro de capture-api, últimos 60 min",
                finding="7% de erro desde 10:02",
                url="https://observabilidade.interno/m/1",
                ok=True,
            ),
            Evaluation(
                tool="search_logs",
                subject="logs de erro de capture-api",
                finding="não foi possível consultar",
                url=None,
                ok=False,
            ),
        ),
        hypotheses=(
            Hypothesis(
                title="Deploy 1.4.2 quebrou a captura",
                confidence=0.8,
                evidence=(
                    Evidence(text="deploy às 10:01", url="https://mudancas.interno/b/9"),
                    Evidence(text="erro começa às 10:02", url=None),
                ),
            ),
            Hypothesis(title="Adquirente lento", confidence=0.15, evidence=()),
        ),
        plan=("Comparar o erro antes e depois do deploy", "Avaliar rollback do 1.4.2"),
        partial_reason=None,
    )


def partial_of(incident_id: UUID) -> Analysis:
    return replace(
        completed(incident_id),
        status=AnalysisStatus.PARTIAL,
        summary="",
        hypotheses=(),
        plan=(),
        partial_reason=PartialReason.TIMEOUT,
    )


@pytest.mark.usefixtures("clean")
async def test_analysis_of_is_none_when_never_requested(
    repository: PostgresIncidentRepository,
) -> None:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)

    assert await repository.analysis_of(incident.id) is None


@pytest.mark.usefixtures("clean")
async def test_requested_analysis_is_pending(repository: PostgresIncidentRepository) -> None:
    incident_id = await requested(repository)

    assert await repository.analysis_of(incident_id) == Analysis(
        incident_id=incident_id,
        status=AnalysisStatus.PENDING,
        requested_at=T0,
        started_at=None,
        finished_at=None,
        summary="",
        evaluations=(),
        hypotheses=(),
        plan=(),
        partial_reason=None,
    )


@pytest.mark.usefixtures("clean")
async def test_second_request_for_the_same_incident_keeps_the_first(
    repository: PostgresIncidentRepository,
) -> None:
    incident_id = await requested(repository)
    async with repository.transaction() as uow:
        await uow.request_analysis(incident_id, T0 + timedelta(minutes=1))

    analysis = await repository.analysis_of(incident_id)
    assert analysis is not None
    assert analysis.requested_at == T0


@pytest.mark.usefixtures("clean")
async def test_claim_takes_the_oldest_pending_and_marks_it_running(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    newer = await requested(repository, offset_seconds=10)
    older = await requested(repository, offset_seconds=0)

    first = await queue.claim(STARTED)
    second = await queue.claim(STARTED)

    assert first is not None
    assert second is not None
    assert (first.incident_id, first.requested_at, first.started_at) == (older, T0, STARTED)
    assert second.incident_id == newer
    assert await queue.claim(STARTED) is None
    analysis = await repository.analysis_of(older)
    assert analysis is not None
    assert (analysis.status, analysis.started_at) == (AnalysisStatus.RUNNING, STARTED)


@pytest.mark.usefixtures("clean")
async def test_simultaneous_claims_never_take_the_same_request(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    pending = {await requested(repository, offset_seconds=i) for i in range(PARALLEL_CLAIMS // 2)}

    jobs = await asyncio.gather(*(queue.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    taken = [job.incident_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(pending)  # cada pedido uma vez, e nenhum perdido


@pytest.mark.usefixtures("clean")
async def test_stuck_lists_only_running_started_before_the_limit(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    old = await requested(repository, offset_seconds=0)
    await queue.claim(T0)
    recent = await requested(repository, offset_seconds=1)
    await queue.claim(T0 + timedelta(minutes=6))
    await requested(repository, offset_seconds=2)  # pendente: não está presa

    stuck = await queue.stuck(T0 + timedelta(minutes=5))

    assert [job.incident_id for job in stuck] == [old]
    assert recent not in {job.incident_id for job in await queue.stuck(T0)}


@pytest.mark.usefixtures("clean")
async def test_finish_writes_analysis_events_card_and_usage_together(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    await queue.claim(STARTED)
    analysis = completed(incident_id)
    event = IncidentEvent(incident_id, FINISHED, EventType.ANALYSIS_COMPLETED, {"hipoteses": 2})
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    await queue.finish(analysis, [event], card, [USAGE, USAGE], FINISHED)

    assert await repository.analysis_of(incident_id) == analysis
    assert await repository.events_of(incident_id) == [event]
    [item] = [
        item
        for item in await PostgresNotificationOutbox(database).due(FINISHED, 10_000)
        if item.notification.incident_id == incident_id
    ]
    assert item.notification == card
    assert await queue.stuck(FINISHED + timedelta(hours=1)) == []
    async with database.engine.connect() as connection:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT stage, model, input_tokens, output_tokens, cache_read_tokens,"
                    " cache_creation_tokens, at FROM llm_usage WHERE incident_id = :id"
                ),
                {"id": incident_id},
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("investigation", "modelo-x", 1200, 300, 800, 100, FINISHED)
    ] * 2


@pytest.mark.usefixtures("clean")
async def test_finish_records_partial_reason_without_usage(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await requested(repository)
    await queue.claim(STARTED)
    analysis = partial_of(incident_id)
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    await queue.finish(analysis, [], card, [], FINISHED)

    assert await repository.analysis_of(incident_id) == analysis


@pytest.mark.usefixtures("clean")
async def test_finish_is_all_or_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    await queue.claim(STARTED)
    analysis = completed(incident_id)
    unknown = IncidentEvent(uuid4(), FINISHED, EventType.ANALYSIS_COMPLETED, {})
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    with pytest.raises(IntegrityError):
        await queue.finish(analysis, [unknown], card, [USAGE], FINISHED)

    stored = await repository.analysis_of(incident_id)
    assert stored is not None
    assert stored.status == AnalysisStatus.RUNNING
    async with database.engine.connect() as connection:
        cards = await connection.execute(
            sa.text("SELECT count(*) FROM outbox WHERE incident_id = :id"), {"id": incident_id}
        )
        usages = await connection.execute(
            sa.text("SELECT count(*) FROM llm_usage WHERE incident_id = :id"), {"id": incident_id}
        )
    assert (cards.scalar_one(), usages.scalar_one()) == (0, 0)


@pytest.mark.usefixtures("clean")
async def test_finish_of_an_analysis_no_longer_running_does_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await requested(repository)
    await queue.claim(STARTED)
    interrupted = replace(partial_of(incident_id), partial_reason=PartialReason.INTERRUPTED)
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=interrupted)
    await queue.finish(interrupted, [], card, [], FINISHED)
    late = completed(incident_id)
    event = IncidentEvent(incident_id, FINISHED, EventType.ANALYSIS_COMPLETED, {})
    late_card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=late)

    await queue.finish(late, [event], late_card, [USAGE], FINISHED)

    assert await repository.analysis_of(incident_id) == interrupted
    assert await repository.events_of(incident_id) == []
    async with database.engine.connect() as connection:
        cards = await connection.execute(
            sa.text("SELECT count(*) FROM outbox WHERE incident_id = :id"), {"id": incident_id}
        )
        usages = await connection.execute(
            sa.text("SELECT count(*) FROM llm_usage WHERE incident_id = :id"), {"id": incident_id}
        )
    assert (cards.scalar_one(), usages.scalar_one()) == (1, 0)


@pytest.mark.usefixtures("clean")
async def test_finish_of_a_pending_analysis_does_nothing(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await requested(repository)
    analysis = completed(incident_id)
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    await queue.finish(analysis, [], card, [USAGE], FINISHED)

    stored = await repository.analysis_of(incident_id)
    assert stored is not None
    assert stored.status == AnalysisStatus.PENDING


async def test_outbox_card_without_analysis_key_reads_as_no_analysis(
    repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    """Cards enfileirados antes do S3 não têm a chave `analysis` no JSON."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.enqueue(make_notification(incident.id), T0)
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE outbox SET notification = notification - 'analysis' WHERE incident_id = :id"
            ),
            {"id": incident.id},
        )

    [item] = [
        item
        for item in await PostgresNotificationOutbox(database).due(T0, 10_000)
        if item.notification.incident_id == incident.id
    ]
    assert item.notification == make_notification(incident.id)
