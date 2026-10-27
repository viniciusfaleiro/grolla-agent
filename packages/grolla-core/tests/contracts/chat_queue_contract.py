"""Suítes de contrato do war room: as leituras e escritas novas do repositório
(`WarRoomRepositoryContract`) e a fila das respostas do chat (`ChatQueueContract`).

Ficam fora de `IncidentRepositoryContract` para que um adapter ganhe cada suíte quando a
implementar. As duas rodam também contra o banco real compartilhado: cada teste cria dados
únicos (uuid4), esvazia a fila com `drain` e só confere os próprios registros.

As fixtures `repository` e `queue` precisam enxergar o mesmo armazenamento.

Regra do `answer`: o status da pergunta vem em `reply.reply_status` (`FAILED` para "não
consegui responder", `ANSWERED` ou `None` para resposta normal). A resposta gravada fica com
`reply_status=None` e `reply_to` apontando a pergunta.
"""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.chat import ChatJob, ChatQueue
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_INVESTIGATION, STAGE_WARROOM_CHAT
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState, Severity
from grolla.domain.safety import REFUSALS, Scope
from grolla.testing.builders import T0, make_evaluations, make_incident
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
ANSWERED_AT = T0 + timedelta(seconds=40)
REPLY = "O erro caiu para 0,2% às 10:20."


def message(incident_id: UUID, text: str, at: datetime = T0, author: str = "Ana") -> ChatMessage:
    """Mensagem ainda sem id: quem grava atribui."""
    return ChatMessage(id=0, incident_id=incident_id, author=author, text=text, at=at)


async def asked(repository: IncidentRepository, *offsets: int) -> tuple[UUID, list[int]]:
    """Incidente novo com uma pergunta pendente a cada `offset` (segundos depois de T0)."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        saved = [
            await uow.add_message(
                message(incident.id, f"pergunta {n}", T0 + timedelta(seconds=offset)),
                wants_reply=True,
            )
            for n, offset in enumerate(offsets)
        ]
    return incident.id, [m.id for m in saved]


async def drain(queue: ChatQueue, now: datetime = STARTED) -> list[ChatJob]:
    """Reivindica tudo que está pendente, na ordem em que a fila entrega."""
    jobs: list[ChatJob] = []
    while (job := await queue.claim(now)) is not None:
        jobs.append(job)
    return jobs


async def claimed(repository: IncidentRepository, queue: ChatQueue) -> ChatJob:
    """Uma pergunta nova, já em andamento."""
    incident_id, _ = await asked(repository, 0)
    return next(job for job in await drain(queue) if job.incident_id == incident_id)


def reply_to(job: ChatJob, status: ReplyStatus | None = ReplyStatus.ANSWERED) -> ChatMessage:
    return ChatMessage(
        id=0,
        incident_id=job.incident_id,
        author=AGENT_AUTHOR,
        text=REPLY,
        at=ANSWERED_AT,
        evaluations=make_evaluations(),
        reply_status=status,
        reply_to=job.message_id,
    )


def agent_event(job: ChatJob) -> IncidentEvent:
    data = {"reply_to": job.message_id, "ok": True}
    return IncidentEvent(job.incident_id, ANSWERED_AT, EventType.AGENT_MESSAGE, data, AGENT_AUTHOR)


class WarRoomRepositoryContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    async def test_incident_for_update_reads_the_war_room_fields(
        self, repository: IncidentRepository
    ) -> None:
        incident = replace(
            make_incident(state=IncidentState.MITIGATED, severity=Severity.P1),
            commander="Ana",
            handling_at=T0 + timedelta(minutes=4),
            mitigated_at=T0 + timedelta(minutes=20),
        )
        async with repository.transaction() as uow:
            await uow.save(incident)

        async with repository.transaction() as uow:
            assert await uow.incident_for_update(incident.id) == incident
            assert await uow.incident_for_update(uuid4()) is None
        assert await repository.get_incident(incident.id) == incident

    async def test_closed_incidents_are_not_active(self, repository: IncidentRepository) -> None:
        service = f"servico-{uuid4().hex[:8]}"
        handling = make_incident(services=(service,), state=IncidentState.HANDLING)
        resolved = replace(
            make_incident(services=(service,), state=IncidentState.RESOLVED),
            resolved_at=T0 + timedelta(minutes=1),
        )
        discarded = make_incident(
            services=(service,), state=IncidentState.DISCARDED, firing=frozenset({uuid4().hex})
        )
        async with repository.transaction() as uow:
            for incident in (handling, resolved, discarded):
                await uow.save(incident)

        async with repository.transaction() as uow:
            active = await uow.active_incidents(service, T0 - timedelta(minutes=5))
        assert [i.id for i in active] == [handling.id]

    async def test_messages_get_ids_and_are_listed_in_order(
        self, repository: IncidentRepository
    ) -> None:
        incident = make_incident()
        other = make_incident()
        async with repository.transaction() as uow:
            await uow.save(incident)
            await uow.save(other)
            question = await uow.add_message(message(incident.id, "o banco voltou?"), True)
            note = await uow.add_message(
                message(incident.id, "vou olhar", T0 + timedelta(seconds=1), "Bruno"), False
            )
            await uow.add_message(message(other.id, "de outro incidente"), False)

        assert note.id > question.id
        assert question.reply_status is ReplyStatus.PENDING
        assert note.reply_status is None
        assert (question.author, question.text, question.at) == ("Ana", "o banco voltou?", T0)
        assert await repository.chat_of(incident.id) == [question, note]
        assert await repository.chat_of(uuid4()) == []


class ChatQueueContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> ChatQueue:
        raise NotImplementedError

    async def test_claim_takes_the_oldest_pending_question(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        incident_id, (first, second) = await asked(repository, 1, 0)

        mine = [job for job in await drain(queue) if job.incident_id == incident_id]

        assert [job.message_id for job in mine] == [second, first]
        assert mine[1] == ChatJob(
            first, incident_id, "Ana", "pergunta 0", T0 + timedelta(seconds=1), STARTED
        )
        statuses = [m.reply_status for m in await repository.chat_of(incident_id)]
        assert statuses == [ReplyStatus.RUNNING, ReplyStatus.RUNNING]

    async def test_message_without_reply_is_not_claimed(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        incident = make_incident()
        async with repository.transaction() as uow:
            await uow.save(incident)
            await uow.add_message(message(incident.id, "só registro"), wants_reply=False)

        assert all(job.incident_id != incident.id for job in await drain(queue))

    async def test_stuck_lists_running_questions_started_before(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)

        before = await queue.stuck(STARTED + timedelta(seconds=1))
        after = await queue.stuck(STARTED)

        assert job in before
        assert all(j.incident_id != job.incident_id for j in after)

    async def test_answer_records_the_reply_events_and_usage(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)
        usages = [fake_usage(1_000, STAGE_WARROOM_CHAT), fake_usage(500, STAGE_WARROOM_CHAT)]

        await queue.answer(job, reply_to(job), [agent_event(job)], usages, ANSWERED_AT)

        question, answer = await repository.chat_of(job.incident_id)
        assert question.reply_status is ReplyStatus.ANSWERED
        assert answer.id > question.id
        assert answer == replace(reply_to(job), id=answer.id, reply_status=None)
        assert await repository.events_of(job.incident_id) == [agent_event(job)]
        assert await queue.chat_tokens(job.incident_id) == 1_100 + 550
        assert job not in await queue.stuck(ANSWERED_AT + timedelta(hours=1))

    async def test_failed_reply_marks_the_question_failed(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)

        await queue.answer(job, reply_to(job, ReplyStatus.FAILED), [], [], ANSWERED_AT)

        question, answer = await repository.chat_of(job.incident_id)
        assert question.reply_status is ReplyStatus.FAILED
        assert answer.reply_status is None

    async def test_reply_without_status_counts_as_answered(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)

        await queue.answer(job, reply_to(job, None), [], [], ANSWERED_AT)

        question, _ = await repository.chat_of(job.incident_id)
        assert question.reply_status is ReplyStatus.ANSWERED

    async def test_refused_reply_keeps_the_scope(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)
        refused = replace(reply_to(job), text=REFUSALS[Scope.EXECUTION], refusal=Scope.EXECUTION)

        await queue.answer(job, refused, [], [], ANSWERED_AT)

        _, answer = await repository.chat_of(job.incident_id)
        assert answer.refusal is Scope.EXECUTION
        assert answer.text == REFUSALS[Scope.EXECUTION]

    async def test_answer_is_recorded_once(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)
        usage = [fake_usage(1_000, STAGE_WARROOM_CHAT)]

        await queue.answer(job, reply_to(job), [agent_event(job)], usage, ANSWERED_AT)
        await queue.answer(job, reply_to(job), [agent_event(job)], usage, ANSWERED_AT)

        assert len(await repository.chat_of(job.incident_id)) == 2
        assert len(await repository.events_of(job.incident_id)) == 1
        assert await queue.chat_tokens(job.incident_id) == 1_100

    async def test_pending_question_is_not_answered(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        incident_id, (question,) = await asked(repository, 0)
        job = ChatJob(question, incident_id, "Ana", "pergunta 0", T0, STARTED)

        await queue.answer(job, reply_to(job), [], [], ANSWERED_AT)

        (pending,) = await repository.chat_of(incident_id)
        assert pending.reply_status is ReplyStatus.PENDING
        await drain(queue)  # não deixa a pergunta pendente para os outros testes

    async def test_chat_tokens_count_only_the_chat_stage(
        self, repository: IncidentRepository, queue: ChatQueue
    ) -> None:
        job = await claimed(repository, queue)
        usages = [fake_usage(1_000, STAGE_WARROOM_CHAT), fake_usage(9_000, STAGE_INVESTIGATION)]

        await queue.answer(job, reply_to(job), [], usages, ANSWERED_AT)

        assert await queue.chat_tokens(job.incident_id) == 1_100
        assert await queue.chat_tokens(uuid4()) == 0
