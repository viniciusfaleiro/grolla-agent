"""Repositório de incidentes e outbox sobre Postgres real.

O banco é compartilhado pela sessão de testes: cada teste usa ids e fingerprints únicos.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresNotificationOutbox,
    migrate,
)
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
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)


class SimulatedError(Exception):
    pass


@pytest.fixture(scope="session")
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    migrate(database_url)
    database = PostgresDatabase(database_url)
    yield database
    await database.dispose()


@pytest.fixture
def repository(database: PostgresDatabase) -> PostgresIncidentRepository:
    return PostgresIncidentRepository(database)


@pytest.fixture
def outbox(database: PostgresDatabase) -> PostgresNotificationOutbox:
    return PostgresNotificationOutbox(database)


def make_alert(
    fingerprint: str | None = None, status: AlertStatus = AlertStatus.FIRING, service: str = "svc"
) -> Alert:
    return Alert(
        fingerprint=fingerprint or uuid4().hex,
        status=status,
        rule_name="Queda de aprovação",
        service=service,
        labels={"service_name": service, "team": "credenciadora"},
        summary="aprovação caiu",
        description="taxa de aprovação abaixo de 90%",
        starts_at=T0,
        ends_at=None if status is AlertStatus.FIRING else T0 + timedelta(minutes=5),
        source_url="https://observabilidade/alerta/1",
        raw={"labels": {"alertname": "Queda de aprovação"}, "valor": 0.87, "lista": [1, None]},
    )


def make_incident(
    service: str | None = None,
    firing: frozenset[str] = frozenset(),
    last_alert_at: datetime = T0,
    detected_at: datetime = T0,
) -> Incident:
    return Incident(
        id=uuid4(),
        title="Queda de aprovação no authorizer-api",
        state=IncidentState.DETECTED,
        severity=Severity.P1,
        services=(service or uuid4().hex, "outro"),
        detected_at=detected_at,
        last_alert_at=last_alert_at,
        firing=firing,
    )


def make_notification(incident_id: UUID, kind: NotificationKind) -> Notification:
    return Notification(
        kind=kind,
        incident_id=incident_id,
        title="Queda de aprovação",
        severity=Severity.P0,
        services=("authorizer-api",),
        summary="aprovação caiu",
        evidence="taxa abaixo de 90%",
        source_url=None,
        page_url=f"https://grolla/incidents/{incident_id}",
    )


async def test_register_alert_is_true_only_the_first_time(
    repository: PostgresIncidentRepository,
) -> None:
    alert = make_alert()
    async with repository.transaction() as work:
        assert await work.register_alert(alert)
    async with repository.transaction() as work:
        assert not await work.register_alert(alert)
        # Mesmo fingerprint e início, outro status, é outro alerta.
        resolved = make_alert(alert.fingerprint, AlertStatus.RESOLVED)
        assert await work.register_alert(resolved)


async def test_transaction_commits_everything_together(
    repository: PostgresIncidentRepository,
) -> None:
    alert = make_alert()
    incident = make_incident(firing=frozenset({alert.fingerprint}))
    event = IncidentEvent(
        incident.id, T0, EventType.ALERT_RECEIVED, {"fingerprint": alert.fingerprint, "n": 1}
    )

    async with repository.transaction() as work:
        await work.register_alert(alert)
        await work.save(incident)
        await work.append([event])
        await work.link_alert(alert, incident.id)

    assert await repository.get_incident(incident.id) == incident
    assert await repository.alerts_of(incident.id) == [alert]
    assert await repository.events_of(incident.id) == [event]


async def test_transaction_rolls_back_on_error(repository: PostgresIncidentRepository) -> None:
    alert = make_alert()
    incident = make_incident()

    async def write_then_fail() -> None:
        async with repository.transaction() as work:
            await work.register_alert(alert)
            await work.save(incident)
            raise SimulatedError

    with pytest.raises(SimulatedError):
        await write_then_fail()

    assert await repository.get_incident(incident.id) is None
    async with repository.transaction() as work:
        assert await work.register_alert(alert)


async def test_save_updates_existing_incident(repository: PostgresIncidentRepository) -> None:
    incident = make_incident(firing=frozenset({"a"}))
    changed = Incident(
        id=incident.id,
        title="novo título",
        state=IncidentState.INFORMED,
        severity=Severity.P3,
        services=(*incident.services, "terceiro"),
        detected_at=incident.detected_at,
        last_alert_at=T0 + timedelta(minutes=3),
        firing=frozenset(),
    )
    async with repository.transaction() as work:
        await work.save(incident)
    async with repository.transaction() as work:
        await work.save(changed)

    assert await repository.get_incident(incident.id) == changed


async def test_active_incidents_by_service_window_and_firing(
    repository: PostgresIncidentRepository,
) -> None:
    service = uuid4().hex
    since = T0 + timedelta(minutes=10)
    stale = make_incident(service, last_alert_at=T0)
    firing = make_incident(
        service, firing=frozenset({"f"}), last_alert_at=T0 + timedelta(minutes=1)
    )
    recent = make_incident(service, last_alert_at=T0 + timedelta(minutes=12))
    other_service = make_incident(last_alert_at=T0 + timedelta(minutes=12))
    async with repository.transaction() as work:
        for incident in (stale, firing, recent, other_service):
            await work.save(incident)

    async with repository.transaction() as work:
        assert await work.active_incidents(service, since) == [recent, firing]


async def test_incident_firing_finds_the_fingerprint(
    repository: PostgresIncidentRepository,
) -> None:
    fingerprint = uuid4().hex
    incident = make_incident(firing=frozenset({fingerprint, uuid4().hex}))
    async with repository.transaction() as work:
        await work.save(incident)

    async with repository.transaction() as work:
        assert await work.incident_firing(fingerprint) == incident
        assert await work.incident_firing(uuid4().hex) is None


async def test_list_incidents_newest_first(repository: PostgresIncidentRepository) -> None:
    future = T0 + timedelta(days=3650)  # à frente dos incidentes dos outros testes
    older = make_incident(detected_at=future)
    newer = make_incident(detected_at=future + timedelta(minutes=1))
    async with repository.transaction() as work:
        await work.save(older)
        await work.save(newer)

    assert await repository.list_incidents(2) == [newer, older]


async def test_events_of_in_chronological_order(repository: PostgresIncidentRepository) -> None:
    incident = make_incident()
    first = IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {})
    second = IncidentEvent(incident.id, T0, EventType.STATE_CHANGED, {"para": "informado"})
    later = IncidentEvent(
        incident.id, T0 + timedelta(seconds=1), EventType.ALERT_GROUPED, {"motivo": "serviço"}
    )
    async with repository.transaction() as work:
        await work.save(incident)
        await work.append([later])
        await work.append([first, second])
        await work.append([])

    assert await repository.events_of(incident.id) == [first, second, later]


async def test_event_data_stores_datetimes_and_ids_as_text(
    repository: PostgresIncidentRepository,
) -> None:
    incident = make_incident()
    event = IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {"at": T0, "id": incident.id})
    async with repository.transaction() as work:
        await work.save(incident)
        await work.append([event])

    [stored] = await repository.events_of(incident.id)
    assert stored.data == {"at": T0.isoformat(), "id": str(incident.id)}


async def test_event_log_refuses_update_and_delete(
    repository: PostgresIncidentRepository, database: PostgresDatabase
) -> None:
    incident = make_incident()
    async with repository.transaction() as work:
        await work.save(incident)
        await work.append([IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {})])

    for statement in (
        "UPDATE incident_events SET author = 'alguém' WHERE incident_id = :id",
        "DELETE FROM incident_events WHERE incident_id = :id",
    ):
        with pytest.raises(DBAPIError, match="só aceita inserção"):
            async with database.engine.begin() as connection:
                await connection.execute(sa.text(statement), {"id": incident.id})

    assert len(await repository.events_of(incident.id)) == 1


async def enqueue(
    repository: PostgresIncidentRepository, *kinds: NotificationKind
) -> tuple[Incident, list[Notification]]:
    incident = make_incident()
    notifications = [make_notification(incident.id, kind) for kind in kinds]
    async with repository.transaction() as work:
        await work.save(incident)
        for notification in notifications:
            await work.enqueue(notification, T0)
    return incident, notifications


async def due_of(
    outbox: PostgresNotificationOutbox, incident: Incident, now: datetime
) -> list[Notification]:
    items = await outbox.due(now, 1000)
    return [item.notification for item in items if item.notification.incident_id == incident.id]


async def test_outbox_due_in_creation_order_from_enqueue_time(
    repository: PostgresIncidentRepository, outbox: PostgresNotificationOutbox
) -> None:
    incident, notifications = await enqueue(
        repository, NotificationKind.INCIDENT_OPENED, NotificationKind.SEVERITY_RAISED
    )

    assert await due_of(outbox, incident, T0 - timedelta(seconds=1)) == []
    assert await due_of(outbox, incident, T0) == notifications


async def test_outbox_delivered_leaves_queue_and_records_event(
    repository: PostgresIncidentRepository, outbox: PostgresNotificationOutbox
) -> None:
    incident, _ = await enqueue(repository, NotificationKind.INCIDENT_OPENED)
    [item] = [i for i in await outbox.due(T0, 1000) if i.notification.incident_id == incident.id]
    assert item.attempts == 0

    at = T0 + timedelta(seconds=2)
    await outbox.delivered(item, at)

    assert await due_of(outbox, incident, at + timedelta(days=1)) == []
    assert await repository.events_of(incident.id) == [
        IncidentEvent(
            incident.id,
            at,
            EventType.NOTIFICATION_DELIVERED,
            {"kind": "incident_opened", "attempts": 1},
        )
    ]


async def test_outbox_retry_later_reschedules_and_counts(
    repository: PostgresIncidentRepository, outbox: PostgresNotificationOutbox
) -> None:
    incident, _ = await enqueue(repository, NotificationKind.INCIDENT_NORMALIZED)
    [item] = [i for i in await outbox.due(T0, 1000) if i.notification.incident_id == incident.id]

    retry_at = T0 + timedelta(seconds=10)
    await outbox.retry_later(item, retry_at, "503")

    assert await due_of(outbox, incident, retry_at - timedelta(seconds=1)) == []
    [again] = [
        i for i in await outbox.due(retry_at, 1000) if i.notification.incident_id == incident.id
    ]
    assert again.attempts == 1
    assert again.notification == item.notification
    assert await repository.events_of(incident.id) == []


async def test_outbox_give_up_leaves_queue_and_records_failure(
    repository: PostgresIncidentRepository, outbox: PostgresNotificationOutbox
) -> None:
    incident, _ = await enqueue(repository, NotificationKind.SEVERITY_RAISED)
    [item] = [i for i in await outbox.due(T0, 1000) if i.notification.incident_id == incident.id]

    at = T0 + timedelta(minutes=5)
    await outbox.give_up(item, at, "timeout")

    assert await due_of(outbox, incident, at + timedelta(days=1)) == []
    assert await repository.events_of(incident.id) == [
        IncidentEvent(
            incident.id,
            at,
            EventType.NOTIFICATION_FAILED,
            {"kind": "severity_raised", "attempts": 1, "error": "timeout"},
        )
    ]
