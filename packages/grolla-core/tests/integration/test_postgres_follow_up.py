"""Fila de ciclos de acompanhamento, pedido antecipado e card do ciclo sobre Postgres real.

A fila pega o ciclo pendente mais antigo de todo o banco, então este módulo usa um banco
próprio dentro do container da sessão: ciclos deixados por outros testes não interferem.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from contracts.cycle_queue_contract import CycleQueueContract
from grolla.adapters.postgres import (
    PostgresAnalysisQueue,
    PostgresCycleQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.analysis import AnalysisQueue
from grolla.application.follow_up import CycleCandidate, CycleQueue
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.application.investigation import STAGE_FOLLOW_UP, STAGE_INVESTIGATION, LlmUsage
from grolla.domain.analysis import AnalysisStatus, Evaluation, Evidence
from grolla.domain.follow_up import Cycle, CycleStatus, Trend
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import T0, make_incident, make_notification
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(minutes=5)
FINISHED = T0 + timedelta(minutes=6)


def usage(stage: str, tokens: int) -> LlmUsage:
    return LlmUsage(
        stage=stage,
        model="modelo-x",
        input_tokens=tokens,
        output_tokens=10,
        cache_read_tokens=5,
        cache_creation_tokens=1,
    )


@pytest.fixture(scope="module")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"ciclos_{uuid4().hex[:8]}"
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
    """Cada teste começa sem ciclos, pedidos, cards nem uso (o log de eventos não se apaga)."""
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text("TRUNCATE cycles, follow_ups, analyses, verifications, outbox, llm_usage")
        )


class TestPostgresCycleQueueContract(CycleQueueContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def queue(self, database: PostgresDatabase) -> CycleQueue:
        return PostgresCycleQueue(database)

    @pytest.fixture
    def analysis_queue(self, database: PostgresDatabase) -> AnalysisQueue:
        return PostgresAnalysisQueue(database)

    @pytest.fixture
    def outbox(self, database: PostgresDatabase) -> NotificationOutbox:
        return PostgresNotificationOutbox(database)


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def queue(database: PostgresDatabase) -> PostgresCycleQueue:
    return PostgresCycleQueue(database)


async def saved(
    repository: PostgresIncidentRepository,
    *,
    state: IncidentState = IncidentState.HANDLING,
    severity: Severity = Severity.P1,
) -> Incident:
    incident = make_incident(state=state, severity=severity)
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident


async def candidate_of(queue: PostgresCycleQueue, incident_id: UUID) -> CycleCandidate:
    """O candidato do incidente; os incidentes de outros testes continuam abertos no banco."""
    [candidate] = [c for c in await queue.candidates({Severity.P1}) if c.incident.id == incident_id]
    return candidate


async def running(queue: PostgresCycleQueue, incident_id: UUID, number: int = 1) -> Cycle:
    assert await queue.enqueue(incident_id, number, T0)
    cycle = await queue.claim(STARTED)
    assert cycle is not None
    return cycle


def finished(cycle: Cycle) -> Cycle:
    return replace(
        cycle,
        status=CycleStatus.COMPLETED,
        finished_at=FINISHED,
        trend=Trend.BETTER,
        error_rate=2.5,
        summary="Erro caiu de 7% para 2,5% depois do rollback.",
        changed=True,
        evidence=(Evidence(text="erro em 2,5%", url="https://observabilidade.interno/m/1"),),
        evaluations=(
            Evaluation(
                tool="query_metric",
                subject="taxa_de_erro de capture-api",
                finding="2,5% de erro",
                url="https://observabilidade.interno/m/1",
                ok=True,
            ),
        ),
        posted=True,
    )


async def test_claim_without_pending_is_none(queue: PostgresCycleQueue) -> None:
    assert await queue.claim(STARTED) is None


async def test_parallel_claims_take_different_cycles(
    repository: PostgresIncidentRepository, queue: PostgresCycleQueue
) -> None:
    incident = await saved(repository)
    for number in range(1, PARALLEL_CLAIMS + 1):
        await queue.enqueue(incident.id, number, T0)

    claimed = await asyncio.gather(*(queue.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    assert sorted(c.number for c in claimed if c is not None) == list(range(1, PARALLEL_CLAIMS + 1))


async def test_finish_records_cycle_events_cards_and_usage_and_clears_the_request(
    repository: PostgresIncidentRepository,
    queue: PostgresCycleQueue,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    cycle = await running(queue, incident.id)
    async with repository.transaction() as uow:
        await uow.request_cycle(incident.id, T0 + timedelta(minutes=3))
    done = finished(cycle)
    event = IncidentEvent(
        incident_id=incident.id,
        at=FINISHED,
        type=EventType.CYCLE_COMPLETED,
        data={"number": 1, "trend": "melhorando"},
    )
    update = make_notification(
        incident.id, NotificationKind.FOLLOW_UP_UPDATE, cycle=done, impact="R$ 30,00 estimado"
    )
    proposal = make_notification(incident.id, NotificationKind.AGENT_PROPOSAL)

    await queue.finish(done, [event], [update, proposal], [usage(STAGE_FOLLOW_UP, 100)], FINISHED)

    assert await repository.cycles_of(incident.id) == [done]
    assert [e.type for e in await repository.events_of(incident.id)] == [EventType.CYCLE_COMPLETED]
    cards = await PostgresNotificationOutbox(database).due(FINISHED, 10)
    assert [card.notification for card in cards] == [update, proposal]
    candidate = await candidate_of(queue, incident.id)
    assert candidate.requested_at is None
    assert candidate.tokens == 116


async def test_request_made_while_the_cycle_runs_survives_the_finish(
    repository: PostgresIncidentRepository, queue: PostgresCycleQueue
) -> None:
    """Um alerta ou mensagem que chega durante o ciclo pede outro ciclo: o pedido fica."""
    incident = await saved(repository)
    cycle = await running(queue, incident.id)
    during = STARTED + timedelta(seconds=30)
    async with repository.transaction() as uow:
        await uow.request_cycle(incident.id, during)

    await queue.finish(finished(cycle), [], [], [], FINISHED)

    assert (await candidate_of(queue, incident.id)).requested_at == during


async def test_candidate_carries_analysis_end_cycles_request_and_follow_up_tokens(
    repository: PostgresIncidentRepository,
    queue: PostgresCycleQueue,
    database: PostgresDatabase,
) -> None:
    incident = await saved(repository)
    async with repository.transaction() as uow:
        await uow.request_analysis(incident.id, T0)
        await uow.request_cycle(incident.id, T0 + timedelta(minutes=2))
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text(
                "UPDATE analyses SET status = :done, finished_at = :at WHERE incident_id = :id"
            ),
            {
                "done": AnalysisStatus.COMPLETED.value,
                "at": T0 + timedelta(minutes=1),
                "id": incident.id,
            },
        )
    first = await running(queue, incident.id)
    await queue.finish(
        finished(first),
        [],
        [],
        [usage(STAGE_FOLLOW_UP, 100), usage(STAGE_INVESTIGATION, 9000)],
        FINISHED,
    )
    await queue.enqueue(incident.id, 2, FINISHED)

    candidate = await candidate_of(queue, incident.id)

    assert candidate.incident == incident
    assert candidate.analysis_done_at == T0 + timedelta(minutes=1)
    assert [c.number for c in candidate.cycles] == [1, 2]
    assert candidate.cycles[0] == finished(first)
    assert candidate.requested_at is None  # o ciclo terminado zerou o pedido
    assert candidate.tokens == 116


async def test_outbox_reads_cards_written_before_the_cycle_keys(
    repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    incident = await saved(repository)
    card = make_notification(incident.id)
    data = {
        "kind": card.kind.value,
        "incident_id": str(incident.id),
        "title": card.title,
        "severity": card.severity.value,
        "services": list(card.services),
        "summary": card.summary,
        "evidence": card.evidence,
        "source_url": card.source_url,
        "page_url": card.page_url,
        "analysis": None,
        "verification": None,
    }
    async with database.engine.begin() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO outbox (incident_id, notification, created_at, next_attempt_at)"
                " VALUES (:id, CAST(:data AS jsonb), :at, :at)"
            ),
            {"id": incident.id, "data": json.dumps(data), "at": T0},
        )

    [item] = await PostgresNotificationOutbox(database).due(T0, 10)

    assert item.notification == card
