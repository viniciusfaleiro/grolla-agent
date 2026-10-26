"""War room sobre Postgres real: campos novos do incidente, lock das ações e fila do chat.

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
from grolla.adapters.postgres import (
    PostgresChatQueue,
    PostgresDatabase,
    PostgresIncidentRepository,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.investigation import STAGE_WARROOM_CHAT, LlmUsage
from grolla.domain.analysis import Evaluation
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.testing.builders import T0, make_incident
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

PARALLEL_CLAIMS = 12
STARTED = T0 + timedelta(seconds=5)
ANSWERED = T0 + timedelta(seconds=40)
LOCK_WAIT = 0.3  # segundos: tempo de a segunda transação mostrar que está esperando
EVALUATION = Evaluation(
    tool="query_metric",
    subject="conexões do banco de capture-api",
    finding="voltou ao normal às 10:20",
    url="https://observabilidade.interno/m/2",
    ok=True,
)


def usage(stage: str = STAGE_WARROOM_CHAT, tokens: int = 100) -> LlmUsage:
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


@pytest.fixture
async def clean(database: PostgresDatabase) -> None:
    """Cada teste começa sem perguntas na fila."""
    async with database.engine.begin() as connection:
        await connection.execute(sa.text("UPDATE chat_messages SET reply_status = NULL"))


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


def message(incident_id: UUID, text: str, offset_seconds: int = 0) -> ChatMessage:
    return ChatMessage(
        id=0,
        incident_id=incident_id,
        author="Ana",
        text=text,
        at=T0 + timedelta(seconds=offset_seconds),
    )


async def asked(
    repository: PostgresIncidentRepository, text: str = "o banco voltou?", offset_seconds: int = 0
) -> ChatMessage:
    incident_id = await saved(repository)
    async with repository.transaction() as uow:
        return await uow.add_message(message(incident_id, text, offset_seconds), wants_reply=True)


def agent_reply(question: ChatMessage, status: ReplyStatus | None = None) -> ChatMessage:
    return ChatMessage(
        id=0,
        incident_id=question.incident_id,
        author=AGENT_AUTHOR,
        text="Sim, as conexões voltaram às 10:20.",
        at=ANSWERED,
        evaluations=(EVALUATION,),
        reply_status=status,
    )


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
    async with repository.transaction() as uow:
        locked = await uow.incident_for_update(incident_id)

    assert found is not None
    assert found.commander == "Ana"
    assert found.handling_at == T0 + timedelta(minutes=2)
    assert found.mitigated_at == T0 + timedelta(minutes=10)
    assert found.resolved_at == T0 + timedelta(minutes=30)
    assert locked == found
    assert found in await repository.list_incidents(10_000)


async def test_incident_for_update_of_unknown_incident_is_none(
    repository: PostgresIncidentRepository,
) -> None:
    async with repository.transaction() as uow:
        assert await uow.incident_for_update(uuid4()) is None


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


async def test_active_incidents_ignore_closed_incidents(
    repository: PostgresIncidentRepository,
) -> None:
    service = f"servico-{uuid4().hex[:8]}"
    ids = {
        state: await saved(repository, state=state, services=(service,), firing=frozenset({"fp"}))
        for state in IncidentState
    }

    async with repository.transaction() as uow:
        active = {i.id for i in await uow.active_incidents(service, T0)}

    closed = {IncidentState.RESOLVED, IncidentState.DISCARDED}
    assert active == {ids[state] for state in IncidentState if state not in closed}


async def test_messages_come_back_in_order_with_ids(
    repository: PostgresIncidentRepository,
) -> None:
    incident_id = await saved(repository)
    async with repository.transaction() as uow:
        later = await uow.add_message(message(incident_id, "segunda", 10), wants_reply=False)
        first = await uow.add_message(message(incident_id, "primeira", 0), wants_reply=True)

    chat = await repository.chat_of(incident_id)

    assert [m.text for m in chat] == ["primeira", "segunda"]
    assert chat == [first, later]
    assert first.id != later.id
    assert first.reply_status is ReplyStatus.PENDING
    assert later.reply_status is None
    assert await repository.chat_of(uuid4()) == []


@pytest.mark.usefixtures("clean")
async def test_claim_takes_the_oldest_question_and_marks_it_running(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    newer = await asked(repository, "segunda", offset_seconds=10)
    older = await asked(repository, "primeira", offset_seconds=0)

    job = await queue.claim(STARTED)

    assert job is not None
    assert job.message_id == older.id
    assert job.incident_id == older.incident_id
    assert job.author == "Ana"
    assert job.text == "primeira"
    assert job.asked_at == older.at
    assert job.started_at == STARTED
    [question] = await repository.chat_of(older.incident_id)
    assert question.reply_status is ReplyStatus.RUNNING
    second = await queue.claim(STARTED)
    assert second is not None
    assert second.message_id == newer.id
    assert await queue.claim(STARTED) is None


@pytest.mark.usefixtures("clean")
async def test_simultaneous_claims_never_take_the_same_question(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    pending = {(await asked(repository, offset_seconds=i)).id for i in range(PARALLEL_CLAIMS // 2)}

    jobs = await asyncio.gather(*(queue.claim(STARTED) for _ in range(PARALLEL_CLAIMS)))

    taken = [job.message_id for job in jobs if job is not None]
    assert sorted(taken) == sorted(pending)  # cada pergunta uma vez, e nenhuma perdida


@pytest.mark.usefixtures("clean")
async def test_stuck_lists_only_running_started_before_the_limit(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    old = await asked(repository, offset_seconds=0)
    await queue.claim(T0)
    await asked(repository, offset_seconds=1)
    await queue.claim(T0 + timedelta(minutes=6))
    await asked(repository, offset_seconds=2)  # pendente: não está presa

    stuck = await queue.stuck(T0 + timedelta(minutes=5))

    assert [job.message_id for job in stuck] == [old.id]


@pytest.mark.usefixtures("clean")
async def test_answer_writes_reply_events_and_usage_together(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    question = await asked(repository)
    job = await queue.claim(STARTED)
    assert job is not None
    event = IncidentEvent(
        question.incident_id, ANSWERED, EventType.AGENT_MESSAGE, {"text": "Sim"}, AGENT_AUTHOR
    )

    await queue.answer(job, agent_reply(question), [event], [usage()], ANSWERED)

    asked_again, reply = await repository.chat_of(question.incident_id)
    assert asked_again.reply_status is ReplyStatus.ANSWERED
    assert reply.reply_to == question.id
    assert reply.reply_status is None
    assert reply.author == AGENT_AUTHOR
    assert reply.evaluations == (EVALUATION,)
    assert reply.at == ANSWERED
    assert await repository.events_of(question.incident_id) == [event]
    assert await queue.chat_tokens(question.incident_id) == 116


@pytest.mark.usefixtures("clean")
async def test_failed_reply_marks_the_question_failed(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    question = await asked(repository)
    job = await queue.claim(STARTED)
    assert job is not None

    await queue.answer(job, agent_reply(question, ReplyStatus.FAILED), [], [], ANSWERED)

    asked_again, reply = await repository.chat_of(question.incident_id)
    assert asked_again.reply_status is ReplyStatus.FAILED
    assert reply.reply_to == question.id


@pytest.mark.usefixtures("clean")
async def test_answer_after_the_question_was_closed_writes_nothing(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue
) -> None:
    question = await asked(repository)
    job = await queue.claim(STARTED)
    assert job is not None
    await queue.answer(job, agent_reply(question, ReplyStatus.FAILED), [], [], ANSWERED)
    event = IncidentEvent(question.incident_id, ANSWERED, EventType.AGENT_MESSAGE, {})

    await queue.answer(job, agent_reply(question), [event], [usage()], ANSWERED)

    chat = await repository.chat_of(question.incident_id)
    assert [m.reply_status for m in chat] == [ReplyStatus.FAILED, None]  # uma resposta só
    assert await repository.events_of(question.incident_id) == []
    assert await queue.chat_tokens(question.incident_id) == 0


async def test_chat_tokens_count_only_the_chat_stage_of_the_incident(
    repository: PostgresIncidentRepository, queue: PostgresChatQueue, database: PostgresDatabase
) -> None:
    incident_id = await saved(repository)
    other_id = await saved(repository)
    rows = [
        (incident_id, usage(tokens=100)),
        (incident_id, usage(tokens=200)),
        (incident_id, usage(stage="investigation", tokens=5_000)),
        (other_id, usage(tokens=9_000)),
    ]
    async with database.engine.begin() as connection:
        for owner, item in rows:
            await connection.execute(
                sa.text(
                    "INSERT INTO llm_usage (incident_id, stage, model, input_tokens,"
                    " output_tokens, cache_read_tokens, cache_creation_tokens, at)"
                    " VALUES (:incident_id, :stage, 'modelo-x', :input, 10, 5, 1, :at)"
                ),
                {"incident_id": owner, "stage": item.stage, "input": item.input_tokens, "at": T0},
            )

    assert await queue.chat_tokens(incident_id) == 116 + 216
    assert await queue.chat_tokens(uuid4()) == 0
