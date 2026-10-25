"""Log de eventos, projeção de incidentes, alertas e outbox sobre a migração 0002."""

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.incidents import IncidentUnitOfWork, OutboxItem
from grolla.domain.incidents import (
    Alert,
    AlertStatus,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
)

SYSTEM_AUTHOR = "sistema"
# Primeira chave dos advisory locks por serviço (a segunda é o hash do nome do serviço).
SERVICE_LOCK_NAMESPACE = 7042


def _incident(row: sa.Row[Any]) -> Incident:
    return Incident(
        id=row.id,
        title=row.title,
        state=IncidentState(row.state),
        severity=Severity(row.severity),
        services=tuple(row.services),
        detected_at=row.detected_at,
        last_alert_at=row.last_alert_at,
        firing=frozenset(row.firing),
    )


def _alert(row: sa.Row[Any]) -> Alert:
    return Alert(
        fingerprint=row.fingerprint,
        status=AlertStatus(row.status),
        rule_name=row.rule_name,
        service=row.service,
        labels=row.labels,
        summary=row.summary,
        description=row.description,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        source_url=row.source_url,
        raw=row.raw,
    )


def _json_default(value: object) -> str:
    """Horários e ids que o domínio ponha em `data` viram texto (ISO 8601 e UUID canônico)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"valor sem representação JSON: {type(value).__name__}")


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, default=_json_default)


def _notification_json(notification: Notification) -> str:
    return _json(
        {
            "kind": notification.kind.value,
            "incident_id": str(notification.incident_id),
            "title": notification.title,
            "severity": notification.severity.value,
            "services": list(notification.services),
            "summary": notification.summary,
            "evidence": notification.evidence,
            "source_url": notification.source_url,
            "page_url": notification.page_url,
        }
    )


def _notification(data: Mapping[str, Any]) -> Notification:
    return Notification(
        kind=NotificationKind(data["kind"]),
        incident_id=UUID(data["incident_id"]),
        title=data["title"],
        severity=Severity(data["severity"]),
        services=tuple(data["services"]),
        summary=data["summary"],
        evidence=data["evidence"],
        source_url=data["source_url"],
        page_url=data["page_url"],
    )


async def _append(connection: AsyncConnection, events: Sequence[IncidentEvent]) -> None:
    if not events:
        return
    await connection.execute(
        sa.text(
            "INSERT INTO incident_events (incident_id, at, type, author, data)"
            " VALUES (:incident_id, :at, :type, :author, CAST(:data AS jsonb))"
        ),
        [
            {
                "incident_id": event.incident_id,
                "at": event.at,
                "type": event.type.value,
                "author": event.author,
                "data": _json(event.data),
            }
            for event in events
        ],
    )


class PostgresUnitOfWork:
    """Escritas de uma transação; quem abre e fecha a transação é o repositório."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def register_alert(self, alert: Alert) -> bool:
        result = await self._connection.execute(
            sa.text(
                "INSERT INTO alerts ("
                "fingerprint, status, rule_name, service, labels, summary, description,"
                " starts_at, ends_at, source_url, raw"
                ") VALUES (:fingerprint, :status, :rule_name,"
                " :service, CAST(:labels AS jsonb), :summary, :description, :starts_at, :ends_at,"
                " :source_url, CAST(:raw AS jsonb))"
                " ON CONFLICT (fingerprint, starts_at, status) DO NOTHING RETURNING id"
            ),
            {
                "fingerprint": alert.fingerprint,
                "status": alert.status.value,
                "rule_name": alert.rule_name,
                "service": alert.service,
                "labels": _json(alert.labels),
                "summary": alert.summary,
                "description": alert.description,
                "starts_at": alert.starts_at,
                "ends_at": alert.ends_at,
                "source_url": alert.source_url,
                "raw": _json(alert.raw),
            },
        )
        return result.scalar_one_or_none() is not None

    async def active_incidents(self, service: str, since: datetime) -> list[Incident]:
        # Serializa até o commit as transações do mesmo serviço: dois webhooks simultâneos
        # não podem abrir dois incidentes para o mesmo problema.
        await self._connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:service))"),
            {"namespace": SERVICE_LOCK_NAMESPACE, "service": service},
        )
        result = await self._connection.execute(
            sa.text(
                "SELECT "
                "id, title, state, severity, services, detected_at, last_alert_at, firing"
                " FROM incidents"
                " WHERE :service = ANY (services)"
                " AND (cardinality(firing) > 0 OR last_alert_at >= :since)"
                " ORDER BY last_alert_at DESC FOR UPDATE"
            ),
            {"service": service, "since": since},
        )
        return [_incident(row) for row in result]

    async def incident_firing(self, fingerprint: str) -> Incident | None:
        # FOR UPDATE aqui e em active_incidents: resolução e agrupamento simultâneos no mesmo
        # incidente não perdem a atualização de `firing` um do outro.
        result = await self._connection.execute(
            sa.text(
                "SELECT "
                "id, title, state, severity, services, detected_at, last_alert_at, firing"
                " FROM incidents WHERE :fingerprint = ANY (firing)"
                " ORDER BY last_alert_at DESC LIMIT 1 FOR UPDATE"
            ),
            {"fingerprint": fingerprint},
        )
        row = result.one_or_none()
        return None if row is None else _incident(row)

    async def save(self, incident: Incident) -> None:
        await self._connection.execute(
            sa.text(
                "INSERT INTO incidents ("
                "id, title, state, severity, services, detected_at, last_alert_at, firing"
                ") VALUES (:id, :title, :state,"
                " :severity, CAST(:services AS text[]), :detected_at, :last_alert_at,"
                " CAST(:firing AS text[]))"
                " ON CONFLICT (id) DO UPDATE SET title = excluded.title,"
                " state = excluded.state, severity = excluded.severity,"
                " services = excluded.services, detected_at = excluded.detected_at,"
                " last_alert_at = excluded.last_alert_at, firing = excluded.firing"
            ),
            {
                "id": incident.id,
                "title": incident.title,
                "state": incident.state.value,
                "severity": incident.severity.value,
                "services": list(incident.services),
                "detected_at": incident.detected_at,
                "last_alert_at": incident.last_alert_at,
                "firing": sorted(incident.firing),
            },
        )

    async def append(self, events: Sequence[IncidentEvent]) -> None:
        await _append(self._connection, events)

    async def link_alert(self, alert: Alert, incident_id: UUID) -> None:
        await self._connection.execute(
            sa.text(
                "UPDATE alerts SET incident_id = :incident_id WHERE fingerprint = :fingerprint"
                " AND starts_at = :starts_at AND status = :status"
            ),
            {
                "incident_id": incident_id,
                "fingerprint": alert.fingerprint,
                "starts_at": alert.starts_at,
                "status": alert.status.value,
            },
        )

    async def enqueue(self, notification: Notification, at: datetime) -> None:
        await self._connection.execute(
            sa.text(
                "INSERT INTO outbox (incident_id, notification, created_at, next_attempt_at)"
                " VALUES (:incident_id, CAST(:notification AS jsonb), :at, :at)"
            ),
            {
                "incident_id": notification.incident_id,
                "notification": _notification_json(notification),
                "at": at,
            },
        )


class PostgresIncidentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[IncidentUnitOfWork]:
        """Uma transação: commit ao sair sem erro, rollback em exceção."""
        async with self._engine.begin() as connection:
            yield PostgresUnitOfWork(connection)

    async def list_incidents(self, limit: int) -> list[Incident]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT "
                    "id, title, state, severity, services, detected_at, last_alert_at, firing"
                    " FROM incidents"
                    " ORDER BY detected_at DESC, id LIMIT :limit"
                ),
                {"limit": limit},
            )
            return [_incident(row) for row in result]

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT "
                    "id, title, state, severity, services, detected_at, last_alert_at, firing"
                    " FROM incidents WHERE id = :id"
                ),
                {"id": incident_id},
            )
            row = result.one_or_none()
            return None if row is None else _incident(row)

    async def alerts_of(self, incident_id: UUID) -> list[Alert]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT "
                    "fingerprint, status, rule_name, service, labels, summary, description,"
                    " starts_at, ends_at, source_url, raw"
                    " FROM alerts WHERE incident_id = :id ORDER BY id"
                ),
                {"id": incident_id},
            )
            return [_alert(row) for row in result]

    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, at, type, author, data FROM incident_events"
                    " WHERE incident_id = :id ORDER BY at, id"
                ),
                {"id": incident_id},
            )
            return [
                IncidentEvent(
                    incident_id=row.incident_id,
                    at=row.at,
                    type=EventType(row.type),
                    data=row.data,
                    author=row.author,
                )
                for row in result
            ]


class PostgresNotificationOutbox:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, notification, attempts FROM outbox AS item"
                    " WHERE delivered_at IS NULL AND failed_at IS NULL"
                    " AND next_attempt_at <= :now"
                    # Card mais antigo do incidente esperando retentativa segura os seguintes.
                    " AND NOT EXISTS (SELECT 1 FROM outbox AS older"
                    " WHERE older.incident_id = item.incident_id AND older.id < item.id"
                    " AND older.delivered_at IS NULL AND older.failed_at IS NULL"
                    " AND older.next_attempt_at > :now)"
                    " ORDER BY id LIMIT :limit"
                ),
                {"now": now, "limit": limit},
            )
            return [
                OutboxItem(
                    id=row.id, notification=_notification(row.notification), attempts=row.attempts
                )
                for row in result
            ]

    async def delivered(self, item: OutboxItem, at: datetime) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET delivered_at = :at, attempts = attempts + 1 WHERE id = :id"
                ),
                {"id": item.id, "at": at},
            )
            await _append(
                connection,
                [_outcome(item, at, EventType.NOTIFICATION_DELIVERED, {})],
            )

    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET next_attempt_at = :at, attempts = attempts + 1,"
                    " last_error = :error WHERE id = :id"
                ),
                {"id": item.id, "at": at, "error": error},
            )

    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET failed_at = :at, attempts = attempts + 1,"
                    " last_error = :error WHERE id = :id"
                ),
                {"id": item.id, "at": at, "error": error},
            )
            await _append(
                connection,
                [_outcome(item, at, EventType.NOTIFICATION_FAILED, {"error": error})],
            )


def _outcome(
    item: OutboxItem, at: datetime, type_: EventType, extra: Mapping[str, Any]
) -> IncidentEvent:
    return IncidentEvent(
        incident_id=item.notification.incident_id,
        at=at,
        type=type_,
        data={"kind": item.notification.kind.value, "attempts": item.attempts + 1, **extra},
        author=SYSTEM_AUTHOR,
    )
