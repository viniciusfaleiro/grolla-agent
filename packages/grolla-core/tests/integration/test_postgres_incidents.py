"""Repositório de incidentes e outbox sobre Postgres real.

O banco é compartilhado pela sessão de testes: cada teste usa ids e fingerprints únicos.
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import sqlalchemy as sa
from contracts.incident_repository_contract import IncidentRepositoryContract
from contracts.notification_outbox_contract import NotificationOutboxContract
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
from grolla.application.incidents import IncidentRepository, NotificationOutbox
from grolla.domain.incidents import EventType, IncidentEvent, NotificationKind
from grolla.testing.builders import T0, make_alert, make_incident, make_notification
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    migrate(database_url)
    database = PostgresDatabase(database_url)
    yield database
    await database.dispose()


@pytest.fixture
def postgres_repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


class TestPostgresIncidentRepositoryContract(IncidentRepositoryContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)


class TestPostgresNotificationOutboxContract(NotificationOutboxContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def outbox(self, database: PostgresDatabase) -> NotificationOutbox:
        return PostgresNotificationOutbox(database)


async def test_event_log_refuses_update_and_delete(
    postgres_repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    incident = make_incident()
    async with postgres_repository.transaction() as uow:
        await uow.save(incident)
        await uow.append([IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {})])

    for statement in (
        "UPDATE incident_events SET author = 'alguém' WHERE incident_id = :id",
        "DELETE FROM incident_events WHERE incident_id = :id",
    ):
        with pytest.raises(DBAPIError, match="só aceita inserção"):
            async with database.engine.begin() as connection:
                await connection.execute(sa.text(statement), {"id": incident.id})

    assert len(await postgres_repository.events_of(incident.id)) == 1


async def test_event_data_stores_datetimes_and_ids_as_text(
    postgres_repository: PostgresIncidentRepository,
) -> None:
    incident = make_incident()
    event = IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {"at": T0, "id": incident.id})
    async with postgres_repository.transaction() as uow:
        await uow.save(incident)
        await uow.append([event])

    [stored] = await postgres_repository.events_of(incident.id)
    assert stored.data == {"at": T0.isoformat(), "id": str(incident.id)}


async def test_alert_without_end_and_source_round_trips(
    postgres_repository: PostgresIncidentRepository,
) -> None:
    incident = make_incident()
    alert = make_alert(source_url=None, labels={})
    async with postgres_repository.transaction() as uow:
        await uow.save(incident)
        await uow.register_alert(alert)
        await uow.link_alert(alert, incident.id)

    assert await postgres_repository.alerts_of(incident.id) == [alert]


async def test_outcome_events_carry_kind_attempts_and_error(
    postgres_repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    outbox = PostgresNotificationOutbox(database)
    incident = make_incident()
    async with postgres_repository.transaction() as uow:
        await uow.save(incident)
        await uow.enqueue(make_notification(incident.id), T0)
        await uow.enqueue(make_notification(incident.id, NotificationKind.SEVERITY_RAISED), T0)
    first, second = [
        item
        for item in await outbox.due(T0, 10_000)
        if item.notification.incident_id == incident.id
    ]

    await outbox.delivered(first, T0 + timedelta(seconds=1))
    await outbox.give_up(second, T0 + timedelta(seconds=2), "canal fora do ar")

    assert [event.data for event in await postgres_repository.events_of(incident.id)] == [
        {"kind": "incident_opened", "attempts": 1},
        {"kind": "severity_raised", "attempts": 1, "error": "canal fora do ar"},
    ]
