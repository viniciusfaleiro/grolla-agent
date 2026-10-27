"""Suíte de contrato do `ConsoleStore`: sessões, mensagens e a fila das respostas do console.

O adapter sobrescreve a fixture `store`. A suíte roda também contra o banco real
compartilhado: cada teste cria sessões únicas (uuid4), esvazia a fila com `drain` e só confere
os próprios registros; os tokens do dia são conferidos pela diferença antes e depois.

Regras:
- a pergunta gravada (`create_session`, `add_question`) ganha id e fica `PENDING`;
- a última atividade da sessão (`updated_at`) é a da última pergunta ou resposta, e
  `sessions` lista as mais recentes primeiro;
- o status da pergunta respondida vem em `reply.reply_status` (`FAILED` para "não consegui
  responder", `ANSWERED` ou `None` para resposta normal); a resposta gravada fica com
  `reply_status=None` e `reply_to` apontando a pergunta;
- `answer` só grava se a pergunta ainda está em andamento, e uma vez só;
- `tokens_since` soma entrada, saída e cache dos usos da etapa `console` desde `since`.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.console import ConsoleJob, ConsoleStore
from grolla.application.investigation import STAGE_CONSOLE, STAGE_INVESTIGATION
from grolla.domain.chat import AGENT_AUTHOR, ReplyStatus
from grolla.domain.console import ChartRequest, ConsoleMessage, ConsoleSession
from grolla.testing.builders import T0, make_evaluations
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
ANSWERED_AT = T0 + timedelta(seconds=40)
REPLY = "pedidos-api saudável: erro em 0,2% na última hora."
CHART = ChartRequest("pedidos-api", "taxa_de_erro", 60)


def question(session_id: UUID, text: str, at: datetime = T0, author: str = "Ana") -> ConsoleMessage:
    """Pergunta ainda sem id: quem grava atribui."""
    return ConsoleMessage(id=0, session_id=session_id, author=author, text=text, at=at)


async def opened(
    store: ConsoleStore, at: datetime = T0, text: str = "o autorizador está saudável?"
) -> tuple[ConsoleSession, ConsoleMessage]:
    """Sessão nova com a primeira pergunta, pendente."""
    session = ConsoleSession(uuid4(), text, "Ana", at, at)
    return session, await store.create_session(session, question(session.id, text, at))


async def drain(store: ConsoleStore, now: datetime = STARTED) -> list[ConsoleJob]:
    """Reivindica tudo que está pendente, na ordem em que a fila entrega."""
    jobs: list[ConsoleJob] = []
    while (job := await store.claim(now)) is not None:
        jobs.append(job)
    return jobs


async def claimed(store: ConsoleStore) -> ConsoleJob:
    """Uma pergunta nova, já em andamento."""
    session, _ = await opened(store)
    return next(job for job in await drain(store) if job.session_id == session.id)


def reply_to(job: ConsoleJob, status: ReplyStatus | None = ReplyStatus.ANSWERED) -> ConsoleMessage:
    return ConsoleMessage(
        id=0,
        session_id=job.session_id,
        author=AGENT_AUTHOR,
        text=REPLY,
        at=ANSWERED_AT,
        charts=(CHART, replace(CHART, signal="latencia_p99", minutes=1440)),
        evaluations=make_evaluations(),
        reply_status=status,
        reply_to=job.message_id,
    )


class ConsoleStoreContract:
    @pytest.fixture
    def store(self) -> ConsoleStore:
        raise NotImplementedError

    async def test_new_session_keeps_the_first_question_pending(self, store: ConsoleStore) -> None:
        session, saved = await opened(store)

        assert saved.id > 0
        assert saved.reply_status is ReplyStatus.PENDING
        assert (saved.session_id, saved.author, saved.text, saved.at) == (
            session.id,
            "Ana",
            "o autorizador está saudável?",
            T0,
        )
        assert await store.session(session.id) == session
        assert await store.messages(session.id) == [saved]
        assert await store.session(uuid4()) is None
        assert await store.messages(uuid4()) == []
        await drain(store)

    async def test_question_in_a_session_is_pending_and_updates_the_activity(
        self, store: ConsoleStore
    ) -> None:
        session, first = await opened(store)
        later = T0 + timedelta(minutes=3)

        second = await store.add_question(question(session.id, "e o pedidos-api?", later, "Bia"))

        assert second.id > first.id
        assert second.reply_status is ReplyStatus.PENDING
        assert await store.messages(session.id) == [first, second]
        assert await store.session(session.id) == replace(session, updated_at=later)
        await drain(store)

    async def test_sessions_come_most_recent_activity_first(self, store: ConsoleStore) -> None:
        base = T0 + timedelta(days=3650)  # depois de qualquer outra sessão do banco
        old, _ = await opened(store, base)
        new, _ = await opened(store, base + timedelta(minutes=1))
        await store.add_question(question(old.id, "de novo", base + timedelta(minutes=2)))

        listed = [s.id for s in await store.sessions(500) if s.id in {old.id, new.id}]

        assert listed == [old.id, new.id]
        assert len(await store.sessions(1)) == 1
        await drain(store)

    async def test_link_incident(self, store: ConsoleStore) -> None:
        session, _ = await opened(store)
        incident_id = uuid4()

        await store.link_incident(session.id, incident_id)

        assert await store.session(session.id) == replace(session, incident_id=incident_id)
        await drain(store)

    async def test_claim_takes_the_oldest_pending_question(self, store: ConsoleStore) -> None:
        session, first = await opened(store, T0 + timedelta(seconds=1))
        second = await store.add_question(question(session.id, "pergunta antiga", T0))

        mine = [job for job in await drain(store) if job.session_id == session.id]

        assert [job.message_id for job in mine] == [second.id, first.id]
        assert mine[1] == ConsoleJob(
            first.id, session.id, "Ana", first.text, T0 + timedelta(seconds=1), STARTED
        )
        statuses = [m.reply_status for m in await store.messages(session.id)]
        assert statuses == [ReplyStatus.RUNNING, ReplyStatus.RUNNING]

    async def test_stuck_lists_running_questions_started_before(self, store: ConsoleStore) -> None:
        job = await claimed(store)

        before = await store.stuck(STARTED + timedelta(seconds=1))
        after = await store.stuck(STARTED)

        assert job in before
        assert all(j.session_id != job.session_id for j in after)

    async def test_answer_records_the_reply_charts_and_usage(self, store: ConsoleStore) -> None:
        job = await claimed(store)
        usages = [fake_usage(1_000, STAGE_CONSOLE), fake_usage(500, STAGE_CONSOLE)]
        spent = await store.tokens_since(T0)

        await store.answer(job, reply_to(job), usages, ANSWERED_AT)

        asked, answer = await store.messages(job.session_id)
        assert asked.reply_status is ReplyStatus.ANSWERED
        assert answer.id > asked.id
        assert answer == replace(reply_to(job), id=answer.id, reply_status=None)
        assert await store.tokens_since(T0) - spent == 1_100 + 550
        assert job not in await store.stuck(ANSWERED_AT + timedelta(hours=1))
        session = await store.session(job.session_id)
        assert session is not None
        assert session.updated_at == ANSWERED_AT

    async def test_failed_reply_marks_the_question_failed(self, store: ConsoleStore) -> None:
        job = await claimed(store)

        await store.answer(job, reply_to(job, ReplyStatus.FAILED), [], ANSWERED_AT)

        asked, answer = await store.messages(job.session_id)
        assert asked.reply_status is ReplyStatus.FAILED
        assert answer.reply_status is None

    async def test_reply_without_status_counts_as_answered(self, store: ConsoleStore) -> None:
        job = await claimed(store)

        await store.answer(job, reply_to(job, None), [], ANSWERED_AT)

        asked, _ = await store.messages(job.session_id)
        assert asked.reply_status is ReplyStatus.ANSWERED

    async def test_answer_is_recorded_once(self, store: ConsoleStore) -> None:
        job = await claimed(store)
        usage = [fake_usage(1_000, STAGE_CONSOLE)]
        spent = await store.tokens_since(T0)

        await store.answer(job, reply_to(job), usage, ANSWERED_AT)
        await store.answer(job, reply_to(job), usage, ANSWERED_AT)

        assert len(await store.messages(job.session_id)) == 2
        assert await store.tokens_since(T0) - spent == 1_100

    async def test_pending_question_is_not_answered(self, store: ConsoleStore) -> None:
        session, saved = await opened(store)
        job = ConsoleJob(saved.id, session.id, "Ana", saved.text, T0, STARTED)

        await store.answer(job, reply_to(job), [], ANSWERED_AT)

        assert [m.reply_status for m in await store.messages(session.id)] == [ReplyStatus.PENDING]
        await drain(store)  # não deixa a pergunta pendente para os outros testes

    async def test_tokens_since_count_only_the_console_stage_since(
        self, store: ConsoleStore
    ) -> None:
        job = await claimed(store)
        usages = [fake_usage(1_000, STAGE_CONSOLE), fake_usage(9_000, STAGE_INVESTIGATION)]
        later = ANSWERED_AT + timedelta(seconds=1)
        spent, spent_later = await store.tokens_since(T0), await store.tokens_since(later)

        await store.answer(job, reply_to(job), usages, ANSWERED_AT)

        assert await store.tokens_since(T0) - spent == 1_100
        assert await store.tokens_since(later) == spent_later
