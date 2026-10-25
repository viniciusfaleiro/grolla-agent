"""Fakes em memória dos ports de incidentes: repositório, outbox, canal de chat e entrada."""

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from grolla.application.incidents import (
    InvalidAlertPayloadError,
    NotificationFailedError,
    OutboxItem,
)
from grolla.domain.analysis import Analysis
from grolla.domain.incidents import (
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

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator["InMemoryUnitOfWork"]:
        snapshot = (
            dict(self.incidents),
            list(self.events),
            set(self.seen),
            list(self.links),
            list(self.outbox),
        )
        try:
            yield InMemoryUnitOfWork(self)
        except BaseException:
            self.incidents, self.events, self.seen, self.links, self.outbox = snapshot
            raise

    async def list_incidents(self, limit: int) -> list[Incident]:
        ordered = sorted(self.incidents.values(), key=lambda i: i.detected_at, reverse=True)
        return ordered[:limit]

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        return self.incidents.get(incident_id)

    async def alerts_of(self, incident_id: UUID) -> list[Alert]:
        return [alert for owner, alert in self.links if owner == incident_id]

    async def analysis_of(self, incident_id: UUID) -> Analysis | None:
        raise NotImplementedError

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

    async def request_analysis(self, incident_id: UUID, at: datetime) -> None:
        raise NotImplementedError


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
