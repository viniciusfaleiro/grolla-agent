"""Fila da validação, pedido no `finish` da análise e `verification_of` sobre Postgres real.

Como a fila da análise, a da validação pega o pedido mais antigo de todo o banco: este módulo
usa um banco próprio dentro do container da sessão.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresAnalysisQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.investigation import LlmUsage
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    PartialReason,
    Verdict,
    Verification,
)
from grolla.domain.incidents import EventType, IncidentEvent, NotificationKind
from grolla.testing.builders import T0, make_incident, make_notification
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
ANALYZED = T0 + timedelta(minutes=2)
STARTED = T0 + timedelta(minutes=3)
FINISHED = T0 + timedelta(minutes=6)
USAGE = LlmUsage(
    stage="verification",
    model="modelo-x",
    input_tokens=5000,
    output_tokens=700,
    cache_read_tokens=4000,
    cache_creation_tokens=0,
)


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"validacoes_{uuid4().hex[:8]}"
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
    """Cada teste começa sem pedidos nas filas."""
    async with database.engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM analyses"))
        await connection.execute(sa.text("DELETE FROM verifications"))


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def queue(database: PostgresDatabase) -> PostgresAnalysisQueue:
    return PostgresAnalysisQueue(database)


def analysis_of(incident_id: UUID, *, hypotheses: bool = True) -> Analysis:
    return Analysis(
        incident_id=incident_id,
        status=AnalysisStatus.COMPLETED if hypotheses else AnalysisStatus.PARTIAL,
        requested_at=T0,
        started_at=T0 + timedelta(seconds=5),
        finished_at=ANALYZED,
        summary="Erro subiu depois do deploy 1.4.2." if hypotheses else "",
        evaluations=(),
        hypotheses=(
            Hypothesis(title="Deploy 1.4.2 quebrou a captura", confidence=0.8, evidence=()),
        )
        if hypotheses
        else (),
        plan=(),
        partial_reason=None if hypotheses else PartialReason.TIMEOUT,
    )


async def analyzed(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    offset_seconds: int = 0,
    *,
    hypotheses: bool = True,
) -> UUID:
    """Incidente com a análise inicial encerrada em `ANALYZED + offset_seconds`."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_analysis(incident.id, T0)
    job = await queue.claim(T0 + timedelta(seconds=5))
    assert job is not None
    assert job.incident_id == incident.id
    analysis = analysis_of(incident.id, hypotheses=hypotheses)
    card = make_notification(incident.id, NotificationKind.ANALYSIS_READY, analysis=analysis)
    at = ANALYZED + timedelta(seconds=offset_seconds)
    await queue.finish(analysis, [], card, [], at)
    return incident.id


def pending(incident_id: UUID) -> Verification:
    return Verification(
        incident_id=incident_id,
        status=AnalysisStatus.PENDING,
        requested_at=ANALYZED,
        started_at=None,
        finished_at=None,
        verdicts=(),
        root_cause=None,
        actions=(),
        missing="",
        evaluations=(),
        partial_reason=None,
    )


def confirmed(incident_id: UUID) -> Verification:
    return Verification(
        incident_id=incident_id,
        status=AnalysisStatus.COMPLETED,
        requested_at=ANALYZED,
        started_at=STARTED,
        finished_at=FINISHED,
        verdicts=(
            HypothesisVerdict(
                hypothesis="Deploy 1.4.2 quebrou a captura",
                verdict=Verdict.CONFIRMED,
                reasoning="O erro começa um minuto depois do deploy e só no código novo.",
                evidence=(
                    Evidence(text="deploy às 10:01", url="https://mudancas.interno/b/9"),
                    Evidence(text="NullPointer em CaptureService desde 10:02", url=None),
                ),
            ),
            HypothesisVerdict(
                hypothesis="Adquirente lento",
                verdict=Verdict.REJECTED,
                reasoning="Latência da adquirente estável.",
                evidence=(),
            ),
        ),
        root_cause="O deploy 1.4.2 introduziu um NullPointer na captura.",
        actions=(
            Action(
                action="Fazer rollback do capture-api para o build 1.4.1",
                target="capture-api",
                expected="Taxa de erro volta abaixo de 1%",
                link="https://mudancas.interno/b/9",
            ),
            Action(
                action="Religar o RDS grolla-lab-db",
                target="RDS grolla-lab-db",
                expected="Conexões ao banco voltam",
                link=None,
            ),
        ),
        missing="",
        evaluations=(
            Evaluation(
                tool="search_logs",
                subject="logs WARN+ de capture-api",
                finding="120x [ERROR] NullPointer em CaptureService",
                url="https://observabilidade.interno/l/1",
                ok=True,
            ),
        ),
        partial_reason=None,
    )


def inconclusive(incident_id: UUID, reason: PartialReason) -> Verification:
    return replace(
        confirmed(incident_id),
        status=AnalysisStatus.PARTIAL,
        root_cause=None,
        actions=(),
        missing="Faltou o log do adquirente.",
        partial_reason=reason,
    )


async def count(database: PostgresDatabase, table: str, incident_id: UUID) -> int:
    queries = {
        "outbox": "SELECT count(*) FROM outbox WHERE incident_id = :id",
        "llm_usage": "SELECT count(*) FROM llm_usage WHERE incident_id = :id",
    }
    async with database.engine.connect() as connection:
        result = await connection.execute(sa.text(queries[table]), {"id": incident_id})
        return result.scalar_one()


async def test_verification_of_is_none_when_never_requested(
    repository: PostgresIncidentRepository,
) -> None:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)

    assert await repository.verification_of(incident.id) is None


async def test_finish_of_an_analysis_with_hypotheses_requests_the_verification(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await analyzed(repository, queue)

    assert await repository.verification_of(incident_id) == pending(incident_id)


async def test_finish_of_an_analysis_without_hypotheses_does_not_request_it(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await analyzed(repository, queue, hypotheses=False)

    assert await repository.verification_of(incident_id) is None
    assert await queue.claim_verification(STARTED) is None


async def test_finish_rolled_back_does_not_request_the_verification(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_analysis(incident.id, T0)
    await queue.claim(T0)
    analysis = analysis_of(incident.id)
    unknown = IncidentEvent(uuid4(), ANALYZED, EventType.VERIFICATION_REQUESTED, {})
    card = make_notification(incident.id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    with pytest.raises(IntegrityError):
        await queue.finish(analysis, [unknown], card, [], ANALYZED)

    assert await repository.verification_of(incident.id) is None


async def test_existing_verification_request_keeps_the_first(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await analyzed(repository, queue)
    # Análise pedida de novo e encerrada outra vez: o pedido de validação não é trocado.
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text("UPDATE analyses SET status = 'em_andamento' WHERE incident_id = :id"),
            {"id": incident_id},
        )
    analysis = analysis_of(incident_id)
    card = make_notification(incident_id, NotificationKind.ANALYSIS_READY, analysis=analysis)

    await queue.finish(analysis, [], card, [], ANALYZED + timedelta(hours=1))

    assert await repository.verification_of(incident_id) == pending(incident_id)


async def test_claim_verification_takes_the_oldest_and_marks_it_running(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    newer = await analyzed(repository, queue, offset_seconds=10)
    older = await analyzed(repository, queue, offset_seconds=0)

    first = await queue.claim_verification(STARTED)
    second = await queue.claim_verification(STARTED)

    assert first is not None
    assert second is not None
    assert (first.incident_id, first.requested_at, first.started_at) == (
        older,
        ANALYZED,
        STARTED,
    )
    assert second.incident_id == newer
    assert await queue.claim_verification(STARTED) is None
    assert await queue.claim(STARTED) is None  # as filas são independentes
    assert await repository.verification_of(older) == replace(
        pending(older), status=AnalysisStatus.RUNNING, started_at=STARTED
    )


async def test_simultaneous_claims_never_take_the_same_verification(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    requested = {
        await analyzed(repository, queue, offset_seconds=i) for i in range(PARALLEL_CLAIMS // 2)
    }

    jobs = await asyncio.gather(
        *(queue.claim_verification(STARTED) for _ in range(PARALLEL_CLAIMS))
    )

    taken = [job.incident_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(requested)  # cada pedido uma vez, e nenhum perdido


async def test_stuck_verifications_lists_only_running_started_before_the_limit(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    old = await analyzed(repository, queue, offset_seconds=0)
    await queue.claim_verification(STARTED)
    recent = await analyzed(repository, queue, offset_seconds=1)
    await queue.claim_verification(STARTED + timedelta(minutes=6))
    await analyzed(repository, queue, offset_seconds=2)  # pendente: não está presa

    stuck = await queue.stuck_verifications(STARTED + timedelta(minutes=5))

    assert [job.incident_id for job in stuck] == [old]
    assert recent not in {job.incident_id for job in await queue.stuck_verifications(STARTED)}
    assert await queue.stuck(FINISHED + timedelta(hours=1)) == []  # análises não se misturam


async def test_finish_verification_writes_verification_events_card_and_usage_together(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await analyzed(repository, queue)
    await queue.claim_verification(STARTED)
    verification = confirmed(incident_id)
    event = IncidentEvent(incident_id, FINISHED, EventType.VERIFICATION_COMPLETED, {"ok": 1})
    card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=verification)

    await queue.finish_verification(verification, [event], card, [USAGE], FINISHED)

    assert await repository.verification_of(incident_id) == verification
    assert await repository.events_of(incident_id) == [event]
    cards = [
        item.notification
        for item in await PostgresNotificationOutbox(database).due(FINISHED, 10_000)
        if item.notification.incident_id == incident_id
    ]
    assert cards[-1] == card  # o card da análise inicial vem antes
    assert await count(database, "llm_usage", incident_id) == 1
    assert await queue.stuck_verifications(FINISHED + timedelta(hours=1)) == []


async def test_finish_verification_records_partial_without_usage(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await analyzed(repository, queue)
    await queue.claim_verification(STARTED)
    verification = inconclusive(incident_id, PartialReason.TIMEOUT)
    card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=verification)

    await queue.finish_verification(verification, [], card, [], FINISHED)

    assert await repository.verification_of(incident_id) == verification


async def test_finish_verification_is_all_or_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await analyzed(repository, queue)
    await queue.claim_verification(STARTED)
    verification = confirmed(incident_id)
    unknown = IncidentEvent(uuid4(), FINISHED, EventType.VERIFICATION_COMPLETED, {})
    card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=verification)

    with pytest.raises(IntegrityError):
        await queue.finish_verification(verification, [unknown], card, [USAGE], FINISHED)

    stored = await repository.verification_of(incident_id)
    assert stored is not None
    assert stored.status == AnalysisStatus.RUNNING
    assert await count(database, "outbox", incident_id) == 1  # só o card da análise inicial
    assert await count(database, "llm_usage", incident_id) == 0


async def test_finish_verification_no_longer_running_does_nothing(
    repository: PostgresIncidentRepository,
    queue: PostgresAnalysisQueue,
    database: PostgresDatabase,
) -> None:
    incident_id = await analyzed(repository, queue)
    await queue.claim_verification(STARTED)
    interrupted = inconclusive(incident_id, PartialReason.INTERRUPTED)
    card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=interrupted)
    await queue.finish_verification(interrupted, [], card, [], FINISHED)
    late = confirmed(incident_id)
    event = IncidentEvent(incident_id, FINISHED, EventType.VERIFICATION_COMPLETED, {})
    late_card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=late)

    await queue.finish_verification(late, [event], late_card, [USAGE], FINISHED)

    assert await repository.verification_of(incident_id) == interrupted
    assert await repository.events_of(incident_id) == []
    assert await count(database, "outbox", incident_id) == 2  # inicial e o da interrompida
    assert await count(database, "llm_usage", incident_id) == 0


async def test_finish_verification_of_a_pending_one_does_nothing(
    repository: PostgresIncidentRepository, queue: PostgresAnalysisQueue
) -> None:
    incident_id = await analyzed(repository, queue)
    verification = confirmed(incident_id)
    card = make_notification(incident_id, NotificationKind.ROOT_CAUSE, verification=verification)

    await queue.finish_verification(verification, [], card, [USAGE], FINISHED)

    assert await repository.verification_of(incident_id) == pending(incident_id)


async def test_outbox_card_without_verification_key_reads_as_no_verification(
    repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    """Cards enfileirados antes da 0004 não têm a chave `verification` no JSON."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.enqueue(make_notification(incident.id), T0)
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE outbox SET notification = notification - 'verification'"
                " WHERE incident_id = :id"
            ),
            {"id": incident.id},
        )

    [item] = [
        item
        for item in await PostgresNotificationOutbox(database).due(T0, 10_000)
        if item.notification.incident_id == incident.id
    ]
    assert item.notification == make_notification(incident.id)
