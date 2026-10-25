"""Suíte de contrato de NotificationOutbox (e do `enqueue` da unidade de trabalho).

As fixtures `repository` e `outbox` precisam enxergar o mesmo armazenamento. No banco real
compartilhado, cada teste usa um incidente novo e só confere os itens dele.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.application.incidents import IncidentRepository, NotificationOutbox, OutboxItem
from grolla.domain.incidents import EventType, Notification, NotificationKind
from grolla.testing.builders import T0, make_incident, make_notification


async def open_incident(repository: IncidentRepository) -> UUID:
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
    return incident.id


async def enqueue(
    repository: IncidentRepository, *notifications: Notification, at: datetime = T0
) -> None:
    async with repository.transaction() as uow:
        for notification in notifications:
            await uow.enqueue(notification, at)


async def due_of(outbox: NotificationOutbox, incident_id: UUID, now: datetime) -> list[OutboxItem]:
    items = await outbox.due(now, limit=10_000)
    return [item for item in items if item.notification.incident_id == incident_id]


class NotificationOutboxContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        raise NotImplementedError

    async def test_enqueued_notification_is_due_from_its_time_with_zero_attempts(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        notification = make_notification(incident_id)
        await enqueue(repository, notification, at=T0)

        assert await due_of(outbox, incident_id, T0 - timedelta(seconds=1)) == []
        [item] = await due_of(outbox, incident_id, T0)
        assert item.notification == notification
        assert item.attempts == 0

    async def test_due_keeps_creation_order_and_respects_limit(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        opened = make_notification(incident_id, NotificationKind.INCIDENT_OPENED)
        raised = make_notification(incident_id, NotificationKind.SEVERITY_RAISED)
        normalized = make_notification(incident_id, NotificationKind.INCIDENT_NORMALIZED)
        await enqueue(repository, opened, raised)
        await enqueue(repository, normalized)

        items = await due_of(outbox, incident_id, T0)
        assert [item.notification for item in items] == [opened, raised, normalized]
        assert len(await outbox.due(T0, limit=1)) == 1

    async def test_enqueue_in_a_failed_transaction_is_discarded(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)

        async def enqueue_then_fail() -> None:
            async with repository.transaction() as uow:
                await uow.enqueue(make_notification(incident_id), T0)
                raise RuntimeError("falha no meio")

        with pytest.raises(RuntimeError, match="falha no meio"):
            await enqueue_then_fail()

        assert await due_of(outbox, incident_id, T0) == []

    async def test_delivered_leaves_the_queue_and_records_the_event(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        await enqueue(repository, make_notification(incident_id))
        [item] = await due_of(outbox, incident_id, T0)

        await outbox.delivered(item, T0 + timedelta(seconds=1))

        assert await due_of(outbox, incident_id, T0 + timedelta(hours=1)) == []
        [event] = await repository.events_of(incident_id)
        assert event.type is EventType.NOTIFICATION_DELIVERED
        assert event.at == T0 + timedelta(seconds=1)
        assert event.data["kind"] == NotificationKind.INCIDENT_OPENED.value

    async def test_retry_later_counts_the_attempt_and_waits(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        await enqueue(repository, make_notification(incident_id))
        [item] = await due_of(outbox, incident_id, T0)
        retry_at = T0 + timedelta(seconds=10)

        await outbox.retry_later(item, retry_at, "canal fora do ar")

        assert await due_of(outbox, incident_id, retry_at - timedelta(seconds=1)) == []
        [again] = await due_of(outbox, incident_id, retry_at)
        assert again.id == item.id
        assert again.attempts == 1
        assert await repository.events_of(incident_id) == []

    async def test_give_up_leaves_the_queue_and_records_the_failure(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        await enqueue(repository, make_notification(incident_id))
        [item] = await due_of(outbox, incident_id, T0)

        await outbox.give_up(item, T0 + timedelta(seconds=2), "canal fora do ar")

        assert await due_of(outbox, incident_id, T0 + timedelta(hours=1)) == []
        [event] = await repository.events_of(incident_id)
        assert event.type is EventType.NOTIFICATION_FAILED
        assert event.at == T0 + timedelta(seconds=2)
        assert event.data["kind"] == NotificationKind.INCIDENT_OPENED.value

    async def test_card_waiting_for_retry_holds_the_later_cards_of_its_incident(
        self, repository: IncidentRepository, outbox: NotificationOutbox
    ) -> None:
        incident_id = await open_incident(repository)
        other_id = await open_incident(repository)
        opened = make_notification(incident_id, NotificationKind.INCIDENT_OPENED)
        raised = make_notification(incident_id, NotificationKind.SEVERITY_RAISED)
        other = make_notification(other_id)
        await enqueue(repository, opened, raised, other)
        first, _ = await due_of(outbox, incident_id, T0)
        retry_at = T0 + timedelta(seconds=10)

        await outbox.retry_later(first, retry_at, "canal fora do ar")

        assert await due_of(outbox, incident_id, T0 + timedelta(seconds=5)) == []
        assert len(await due_of(outbox, other_id, T0 + timedelta(seconds=5))) == 1
        items = await due_of(outbox, incident_id, retry_at)
        assert [item.notification for item in items] == [opened, raised]
