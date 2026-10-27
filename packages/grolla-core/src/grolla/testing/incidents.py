"""Fakes em memória dos ports de incidentes: repositório, outbox, fila de análises, canal de
chat e entrada.
"""

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from grolla.application.analysis import AnalysisJob
from grolla.application.chat import ChatJob
from grolla.application.incidents import (
    InvalidAlertPayloadError,
    NotificationFailedError,
    OutboxItem,
)
from grolla.application.investigation import STAGE_WARROOM_CHAT, LlmUsage
from grolla.domain.analysis import Analysis, AnalysisStatus, Verification
from grolla.domain.chat import ChatMessage, ReplyStatus
from grolla.domain.follow_up import Cycle
from grolla.domain.incidents import (
    OPEN_STATES,
    Alert,
    AlertStatus,
    EventType,
    Incident,
    IncidentEvent,
    Notification,
)


@dataclass
class OutboxEntry:
    """Linha da outbox em memória."""

    id: int
    notification: Notification
    next_attempt_at: datetime
    attempts: int = 0
    delivered_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None

    @property
    def pending(self) -> bool:
        return self.delivered_at is None and self.failed_at is None


class InMemoryIncidentRepository:
    """Repositório em memória. O estado é público para os testes inspecionarem."""

    def __init__(self) -> None:
        self.incidents: dict[UUID, Incident] = {}
        self.events: list[IncidentEvent] = []
        self.seen: set[tuple[str, datetime, AlertStatus]] = set()
        self.links: list[tuple[UUID, Alert]] = []
        self.outbox: list[OutboxEntry] = []
        self.analyses: dict[UUID, Analysis] = {}
        self.verifications: dict[UUID, Verification] = {}
        self.usages: list[tuple[UUID, LlmUsage, datetime]] = []  # (incidente, uso, quando)
        self.messages: list[ChatMessage] = []
        self.reply_started: dict[int, datetime] = {}  # pergunta em andamento → início
        self.learning_requests: dict[UUID, datetime] = {}  # incidente → pedido do aprendizado
        self.cycle_requests: dict[UUID, datetime] = {}  # incidente → ciclo antecipado
        self.cycles: dict[tuple[UUID, int], Cycle] = {}  # (incidente, número) → ciclo

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator["InMemoryUnitOfWork"]:
        snapshot = (
            dict(self.incidents),
            list(self.events),
            set(self.seen),
            list(self.links),
            list(self.outbox),
            dict(self.analyses),
            dict(self.verifications),
            list(self.usages),
            list(self.messages),
            dict(self.reply_started),
            dict(self.learning_requests),
            dict(self.cycle_requests),
            dict(self.cycles),
        )
        try:
            yield InMemoryUnitOfWork(self)
        except BaseException:
            (
                self.incidents,
                self.events,
                self.seen,
                self.links,
                self.outbox,
                self.analyses,
                self.verifications,
                self.usages,
                self.messages,
                self.reply_started,
                self.learning_requests,
                self.cycle_requests,
                self.cycles,
            ) = snapshot
            raise

    def add_message(self, message: ChatMessage, wants_reply: bool) -> ChatMessage:
        """Grava com o próximo id; com `wants_reply`, a mensagem fica pendente de resposta."""
        next_id = max((m.id for m in self.messages), default=0) + 1
        status = ReplyStatus.PENDING if wants_reply else message.reply_status
        saved = replace(message, id=next_id, reply_status=status)
        self.messages.append(saved)
        return saved

    async def list_incidents(self, limit: int) -> list[Incident]:
        ordered = sorted(self.incidents.values(), key=lambda i: i.detected_at, reverse=True)
        return ordered[:limit]

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        return self.incidents.get(incident_id)

    async def alerts_of(self, incident_id: UUID) -> list[Alert]:
        return [alert for owner, alert in self.links if owner == incident_id]

    async def chat_of(self, incident_id: UUID) -> list[ChatMessage]:
        mine = [m for m in self.messages if m.incident_id == incident_id]
        return sorted(mine, key=lambda m: (m.at, m.id))

    async def verification_of(self, incident_id: UUID) -> Verification | None:
        return self.verifications.get(incident_id)

    async def analysis_of(self, incident_id: UUID) -> Analysis | None:
        return self.analyses.get(incident_id)

    async def cycles_of(self, incident_id: UUID) -> list[Cycle]:
        return sorted(
            (c for (i, _), c in self.cycles.items() if i == incident_id), key=lambda c: c.number
        )

    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]:
        return [event for event in self.events if event.incident_id == incident_id]


class InMemoryUnitOfWork:
    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self._repository = repository

    async def register_alert(self, alert: Alert) -> bool:
        key = (alert.fingerprint, alert.starts_at, alert.status)
        if key in self._repository.seen:
            return False
        self._repository.seen.add(key)
        return True

    async def active_incidents(self, service: str, since: datetime) -> list[Incident]:
        return sorted(
            (
                incident
                for incident in self._repository.incidents.values()
                if service in incident.services
                and incident.state in OPEN_STATES
                and (incident.firing or incident.last_alert_at >= since)
            ),
            key=lambda i: i.last_alert_at,
            reverse=True,
        )

    async def incident_firing(self, fingerprint: str) -> Incident | None:
        found = [i for i in self._repository.incidents.values() if fingerprint in i.firing]
        return max(found, key=lambda i: i.last_alert_at, default=None)

    async def save(self, incident: Incident) -> None:
        self._repository.incidents[incident.id] = incident

    async def append(self, events: Sequence[IncidentEvent]) -> None:
        self._repository.events.extend(events)

    async def link_alert(self, alert: Alert, incident_id: UUID) -> None:
        self._repository.links.append((incident_id, alert))

    async def enqueue(self, notification: Notification, at: datetime) -> None:
        outbox = self._repository.outbox
        next_id = max((entry.id for entry in outbox), default=0) + 1
        outbox.append(OutboxEntry(next_id, notification, next_attempt_at=at))

    async def incident_for_update(self, incident_id: UUID) -> Incident | None:
        return self._repository.incidents.get(incident_id)  # sem concorrência em memória

    async def add_message(self, message: ChatMessage, wants_reply: bool) -> ChatMessage:
        return self._repository.add_message(message, wants_reply)

    async def request_analysis(self, incident_id: UUID, at: datetime) -> None:
        """Um pedido por incidente: pedir de novo mantém o primeiro."""
        self._repository.analyses.setdefault(
            incident_id,
            Analysis(
                incident_id=incident_id,
                status=AnalysisStatus.PENDING,
                requested_at=at,
                started_at=None,
                finished_at=None,
                summary="",
                evaluations=(),
                hypotheses=(),
                plan=(),
                partial_reason=None,
            ),
        )

    async def request_learning(self, incident_id: UUID, at: datetime) -> None:
        self._repository.learning_requests[incident_id] = at

    async def request_cycle(self, incident_id: UUID, at: datetime) -> None:
        self._repository.cycle_requests[incident_id] = at


class InMemoryAnalysisQueue:
    """Fila de análises sobre o estado de um `InMemoryIncidentRepository`."""

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self._repository = repository

    async def claim(self, now: datetime) -> AnalysisJob | None:
        pending = [
            a for a in self._repository.analyses.values() if a.status is AnalysisStatus.PENDING
        ]
        oldest = min(pending, key=lambda a: a.requested_at, default=None)
        if oldest is None:
            return None
        self._repository.analyses[oldest.incident_id] = replace(
            oldest, status=AnalysisStatus.RUNNING, started_at=now
        )
        return AnalysisJob(oldest.incident_id, oldest.requested_at, now)

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        jobs: list[AnalysisJob] = []
        for a in self._repository.analyses.values():
            if (
                a.status is AnalysisStatus.RUNNING
                and a.started_at
                and a.started_at < started_before
            ):
                jobs.append(AnalysisJob(a.incident_id, a.requested_at, a.started_at))
        return jobs

    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        current = self._repository.analyses.get(analysis.incident_id)
        if current is None or current.status is not AnalysisStatus.RUNNING:
            return  # já encerrada (pelo reaper ou por outro worker): a análise chega uma vez
        async with self._repository.transaction() as uow:
            self._repository.analyses[analysis.incident_id] = analysis
            if analysis.hypotheses:
                self._repository.verifications.setdefault(
                    analysis.incident_id, _pending_verification(analysis.incident_id, at)
                )
            await uow.append(events)
            await uow.enqueue(notification, at)
            self._repository.usages.extend((analysis.incident_id, u, at) for u in usages)

    async def claim_verification(self, now: datetime) -> AnalysisJob | None:
        verifications = self._repository.verifications
        pending = [v for v in verifications.values() if v.status is AnalysisStatus.PENDING]
        oldest = min(pending, key=lambda v: v.requested_at, default=None)
        if oldest is None:
            return None
        verifications[oldest.incident_id] = replace(
            oldest, status=AnalysisStatus.RUNNING, started_at=now
        )
        return AnalysisJob(oldest.incident_id, oldest.requested_at, now)

    async def stuck_verifications(self, started_before: datetime) -> list[AnalysisJob]:
        return [
            AnalysisJob(v.incident_id, v.requested_at, v.started_at)
            for v in self._repository.verifications.values()
            if v.status is AnalysisStatus.RUNNING and v.started_at and v.started_at < started_before
        ]

    async def finish_verification(
        self,
        verification: Verification,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        current = self._repository.verifications.get(verification.incident_id)
        if current is None or current.status is not AnalysisStatus.RUNNING:
            return  # já encerrada: a validação chega uma vez
        async with self._repository.transaction() as uow:
            self._repository.verifications[verification.incident_id] = verification
            await uow.append(events)
            await uow.enqueue(notification, at)
            self._repository.usages.extend((verification.incident_id, u, at) for u in usages)


def _pending_verification(incident_id: UUID, at: datetime) -> Verification:
    return Verification(
        incident_id=incident_id,
        status=AnalysisStatus.PENDING,
        requested_at=at,
        started_at=None,
        finished_at=None,
        verdicts=(),
        root_cause=None,
        actions=(),
        missing="",
        evaluations=(),
        partial_reason=None,
    )


class InMemoryNotificationOutbox:
    """Outbox sobre o estado de um `InMemoryIncidentRepository`."""

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self._repository = repository

    async def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        items: list[OutboxItem] = []
        waiting: set[UUID] = set()  # incidentes com um card mais antigo ainda não vencido
        for entry in sorted(self._repository.outbox, key=lambda e: e.id):
            incident_id = entry.notification.incident_id
            if not entry.pending:
                continue
            if entry.next_attempt_at > now:
                waiting.add(incident_id)
            elif incident_id not in waiting:
                items.append(OutboxItem(entry.id, entry.notification, entry.attempts))
        return items[:limit]

    async def delivered(self, item: OutboxItem, at: datetime) -> None:
        entry = self._entry(item)
        entry.delivered_at = at
        entry.attempts += 1
        self._record(entry, at, EventType.NOTIFICATION_DELIVERED, {})

    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None:
        entry = self._entry(item)
        entry.attempts += 1
        entry.next_attempt_at = at
        entry.last_error = error

    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None:
        entry = self._entry(item)
        entry.attempts += 1
        entry.failed_at = at
        entry.last_error = error
        self._record(entry, at, EventType.NOTIFICATION_FAILED, {"error": error})

    def _entry(self, item: OutboxItem) -> OutboxEntry:
        return next(entry for entry in self._repository.outbox if entry.id == item.id)

    def _record(
        self, entry: OutboxEntry, at: datetime, kind: EventType, extra: Mapping[str, Any]
    ) -> None:
        notification = entry.notification
        data = {"kind": notification.kind.value, "attempts": entry.attempts, **extra}
        self._repository.events.append(IncidentEvent(notification.incident_id, at, kind, data))


class FakeChatNotifier:
    """Canal de chat que grava o que recebeu e falha as próximas `failures` postagens."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.posted: list[Notification] = []
        self.attempts: list[Notification] = []

    async def post(self, notification: Notification) -> None:
        self.attempts.append(notification)
        if self.failures > 0:
            self.failures -= 1
            raise NotificationFailedError("canal fake programado para falhar")
        self.posted.append(notification)


FAKE_AUTHORIZATION = "Basic ZmFrZTpmYWtl"  # fake:fake


class FakeAlertIngress:
    """Entrada de alertas que aceita um cabeçalho fixo e devolve os alertas programados.

    Payload válido é qualquer mapeamento com a chave `alerts`.
    """

    def __init__(
        self, alerts: Sequence[Alert] = (), authorization: str = FAKE_AUTHORIZATION
    ) -> None:
        self.alerts = list(alerts)
        self.authorization = authorization

    def authenticate(self, headers: Mapping[str, str]) -> bool:
        return headers.get("authorization") == self.authorization

    def parse(self, payload: Any) -> list[Alert]:
        if not isinstance(payload, Mapping) or "alerts" not in payload:
            raise InvalidAlertPayloadError("payload sem a lista de alertas")
        return list(self.alerts)


class InMemoryChatQueue:
    """Fila das perguntas do chat sobre o estado de um `InMemoryIncidentRepository`."""

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self._repository = repository

    async def claim(self, now: datetime) -> ChatJob | None:
        pending = [m for m in self._repository.messages if m.reply_status is ReplyStatus.PENDING]
        oldest = min(pending, key=lambda m: (m.at, m.id), default=None)
        if oldest is None:
            return None
        self._set_status(oldest.id, ReplyStatus.RUNNING)
        self._repository.reply_started[oldest.id] = now
        return _job(oldest, now)

    async def stuck(self, started_before: datetime) -> list[ChatJob]:
        started = self._repository.reply_started
        return [
            _job(m, started[m.id])
            for m in self._repository.messages
            if m.reply_status is ReplyStatus.RUNNING and started[m.id] < started_before
        ]

    async def answer(
        self,
        job: ChatJob,
        reply: ChatMessage,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        question = self._message(job.message_id)
        if question is None or question.reply_status is not ReplyStatus.RUNNING:
            return  # já respondida (pelo reaper ou por outro worker): a resposta chega uma vez
        async with self._repository.transaction() as uow:
            self._set_status(job.message_id, reply.reply_status or ReplyStatus.ANSWERED)
            del self._repository.reply_started[job.message_id]
            self._repository.add_message(
                replace(reply, reply_status=None, reply_to=job.message_id), wants_reply=False
            )
            await uow.append(events)
            self._repository.usages.extend((job.incident_id, u, at) for u in usages)

    async def chat_tokens(self, incident_id: UUID) -> int:
        return sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
            for owner, u, _ in self._repository.usages
            if owner == incident_id and u.stage == STAGE_WARROOM_CHAT
        )

    def _message(self, message_id: int) -> ChatMessage | None:
        return next((m for m in self._repository.messages if m.id == message_id), None)

    def _set_status(self, message_id: int, status: ReplyStatus) -> None:
        messages = self._repository.messages
        index = next(n for n, m in enumerate(messages) if m.id == message_id)
        messages[index] = replace(messages[index], reply_status=status)


def _job(question: ChatMessage, started_at: datetime) -> ChatJob:
    return ChatJob(
        message_id=question.id,
        incident_id=question.incident_id,
        author=question.author,
        text=question.text,
        asked_at=question.at,
        started_at=started_at,
    )
