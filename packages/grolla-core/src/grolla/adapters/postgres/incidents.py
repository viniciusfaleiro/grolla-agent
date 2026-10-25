"""Log de eventos, projeção de incidentes, alertas e outbox sobre a migração 0002."""

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from uuid import UUID

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.incidents import IncidentUnitOfWork, OutboxItem
from grolla.domain.incidents import Alert, Incident, IncidentEvent


class PostgresIncidentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    def transaction(self) -> AbstractAsyncContextManager[IncidentUnitOfWork]:
        raise NotImplementedError

    async def list_incidents(self, limit: int) -> list[Incident]:
        raise NotImplementedError

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        raise NotImplementedError

    async def alerts_of(self, incident_id: UUID) -> list[Alert]:
        raise NotImplementedError

    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]:
        raise NotImplementedError


class PostgresNotificationOutbox:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        raise NotImplementedError

    async def delivered(self, item: OutboxItem, at: datetime) -> None:
        raise NotImplementedError

    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None:
        raise NotImplementedError

    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None:
        raise NotImplementedError
