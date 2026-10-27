"""Fake em memória do `ConsoleStore`; o uso do modelo vai para `usages`."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from grolla.application.console import ConsoleJob
from grolla.application.investigation import STAGE_CONSOLE, LlmUsage
from grolla.domain.chat import ReplyStatus
from grolla.domain.console import ConsoleMessage, ConsoleSession


class InMemoryConsoleStore:
    """Sessões, mensagens e a fila de respostas do console em memória.

    A última atividade da sessão (`updated_at`) é a da última pergunta ou resposta. Cada uso
    gravado por `answer` fica em `usages`, com o horário em `usage_times`.
    """

    def __init__(self) -> None:
        self.usages: list[LlmUsage] = []
        self.usage_times: list[datetime] = []
        self._sessions: dict[UUID, ConsoleSession] = {}
        self._messages: list[ConsoleMessage] = []
        self._started: dict[int, datetime] = {}

    async def create_session(
        self, session: ConsoleSession, question: ConsoleMessage
    ) -> ConsoleMessage:
        self._sessions[session.id] = session
        return await self.add_question(question)

    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage:
        saved = self._add(replace(question, reply_status=ReplyStatus.PENDING, reply_to=None))
        self._touch(question.session_id, question.at)
        return saved

    async def sessions(self, limit: int) -> list[ConsoleSession]:
        recent = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        return recent[:limit]

    async def session(self, session_id: UUID) -> ConsoleSession | None:
        return self._sessions.get(session_id)

    async def messages(self, session_id: UUID) -> list[ConsoleMessage]:
        return [m for m in self._messages if m.session_id == session_id]

    def all_messages(self) -> list[ConsoleMessage]:
        """Todas as mensagens de todas as sessões, para os outros fakes (métricas)."""
        return list(self._messages)

    async def link_incident(self, session_id: UUID, incident_id: UUID) -> None:
        session = self._sessions[session_id]
        self._sessions[session_id] = replace(session, incident_id=incident_id)

    async def claim(self, now: datetime) -> ConsoleJob | None:
        pending = [m for m in self._messages if m.reply_status is ReplyStatus.PENDING]
        oldest = min(pending, key=lambda m: (m.at, m.id), default=None)
        if oldest is None:
            return None
        self._set_status(oldest.id, ReplyStatus.RUNNING)
        self._started[oldest.id] = now
        return _job(oldest, now)

    async def stuck(self, started_before: datetime) -> list[ConsoleJob]:
        return [
            _job(m, self._started[m.id])
            for m in self._messages
            if m.reply_status is ReplyStatus.RUNNING and self._started[m.id] < started_before
        ]

    async def answer(
        self, job: ConsoleJob, reply: ConsoleMessage, usages: Sequence[LlmUsage], at: datetime
    ) -> None:
        question = next((m for m in self._messages if m.id == job.message_id), None)
        if question is None or question.reply_status is not ReplyStatus.RUNNING:
            return  # já respondida (pelo reaper ou por outro worker): a resposta chega uma vez
        self._set_status(job.message_id, reply.reply_status or ReplyStatus.ANSWERED)
        del self._started[job.message_id]
        self._add(replace(reply, reply_status=None, reply_to=job.message_id))
        self._touch(job.session_id, at)
        self.usages.extend(usages)
        self.usage_times.extend(at for _ in usages)

    async def tokens_since(self, since: datetime) -> int:
        return sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
            for u, at in zip(self.usages, self.usage_times, strict=True)
            if u.stage == STAGE_CONSOLE and at >= since
        )

    def _add(self, message: ConsoleMessage) -> ConsoleMessage:
        saved = replace(message, id=len(self._messages) + 1)
        self._messages.append(saved)
        return saved

    def _touch(self, session_id: UUID, at: datetime) -> None:
        session = self._sessions[session_id]
        self._sessions[session_id] = replace(session, updated_at=max(session.updated_at, at))

    def _set_status(self, message_id: int, status: ReplyStatus) -> None:
        index = next(n for n, m in enumerate(self._messages) if m.id == message_id)
        self._messages[index] = replace(self._messages[index], reply_status=status)


def _job(question: ConsoleMessage, started_at: datetime) -> ConsoleJob:
    return ConsoleJob(
        message_id=question.id,
        session_id=question.session_id,
        author=question.author,
        text=question.text,
        asked_at=question.at,
        started_at=started_at,
    )
