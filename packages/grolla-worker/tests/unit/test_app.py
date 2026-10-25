from collections.abc import AsyncIterator
from typing import Any

import grolla_worker.app
import pytest
from grolla.adapters.postgres import PostgresDatabase
from grolla.application.clock import Clock
from grolla.application.incidents import ChatNotifier, NotificationOutbox
from grolla.bootstrap import WorkerContainer, WorkerSettings
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import (
    FakeChatNotifier,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)
from grolla_worker.app import DELIVERY_CRON, build_app
from pydantic import SecretStr


def registered(container: WorkerContainer) -> Any:
    """O app montado, como Any: os tipos genéricos do procrastinate ficam parcialmente
    desconhecidos para o pyright strict."""
    return build_app(container)


DATABASE_URL = "postgresql://grolla:grolla@localhost:5433/grolla"  # nunca é conectado aqui


@pytest.fixture
async def container() -> AsyncIterator[WorkerContainer]:
    database = PostgresDatabase(DATABASE_URL)
    yield WorkerContainer(
        settings=WorkerSettings(
            database_url=DATABASE_URL, teams_webhook_url=SecretStr("http://chat")
        ),
        clock=FakeClock(),
        database=database,
        outbox=InMemoryNotificationOutbox(InMemoryIncidentRepository()),
        notifier=FakeChatNotifier(),
    )
    await database.dispose()


def test_delivery_runs_every_five_seconds_one_round_at_a_time(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    task = app.tasks["deliver_notifications"]
    assert task.queueing_lock == "deliver_notifications"
    assert task.lock == "deliver_notifications"
    periodic = app.periodic_registry.periodic_tasks[("deliver_notifications", "")]
    assert periodic.cron == DELIVERY_CRON == "* * * * * */5"


async def test_delivery_job_delivers_the_container_outbox(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[NotificationOutbox, ChatNotifier, Clock]] = []

    async def deliver(outbox: NotificationOutbox, notifier: ChatNotifier, clock: Clock) -> int:
        calls.append((outbox, notifier, clock))
        return 0

    monkeypatch.setattr(grolla_worker.app, "deliver_notifications", deliver)
    app = registered(container)

    await app.tasks["deliver_notifications"].func(timestamp=0)

    assert calls == [(container.outbox, container.notifier, container.clock)]
