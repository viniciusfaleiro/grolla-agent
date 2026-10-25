from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import grolla_worker.app
import pytest
from grolla.adapters.postgres import PostgresDatabase
from grolla.application.clock import Clock
from grolla.application.incidents import ChatNotifier, NotificationOutbox
from grolla.bootstrap import TeamConfig, WorkerContainer, WorkerSettings
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import (
    FakeChatNotifier,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)
from grolla_worker.app import (
    ANALYSES_CRON,
    ANALYSIS_QUEUE,
    DEFAULT_QUEUE,
    DELIVERY_CRON,
    REAPER_CRON,
    build_app,
)
from pydantic import SecretStr


def registered(container: WorkerContainer) -> Any:
    """O app montado, como Any: os tipos genéricos do procrastinate ficam parcialmente
    desconhecidos para o pyright strict."""
    return build_app(container)


DATABASE_URL = "postgresql://grolla:grolla@localhost:5433/grolla"  # nunca é conectado aqui


@pytest.fixture
async def container() -> AsyncIterator[WorkerContainer]:
    database = PostgresDatabase(DATABASE_URL)
    repository = InMemoryIncidentRepository()
    config = TeamConfig(team="t", page_base_url="http://grolla")
    pending = cast(Any, None)  # provisório: os fakes da investigação chegam do grolla-dominio
    yield WorkerContainer(
        settings=WorkerSettings(
            database_url=DATABASE_URL,
            config_file=Path("/nao-usado.yaml"),
            teams_webhook_url=SecretStr("http://chat"),
            anthropic_api_key=SecretStr("chave"),
            azure_devops_token=SecretStr("token"),
        ),
        config=config,
        clock=FakeClock(),
        database=database,
        outbox=InMemoryNotificationOutbox(repository),
        notifier=FakeChatNotifier(),
        repository=repository,
        analysis_queue=pending,
        model=pending,
        telemetry=pending,
        changes=pending,
        analysis=config.analysis_policy(),
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


def test_heartbeat_and_delivery_run_in_the_default_queue_and_analyses_in_their_own(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    queues = {name: task.queue for name, task in app.tasks.items() if task.queue != "builtin"}

    assert queues == {
        "heartbeat": DEFAULT_QUEUE,
        "deliver_notifications": DEFAULT_QUEUE,
        "run_analyses": ANALYSIS_QUEUE,
        "reap_stuck_analyses": ANALYSIS_QUEUE,
    }


def test_analyses_run_every_five_seconds_in_parallel_and_the_reaper_once_a_minute(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    analyses = app.tasks["run_analyses"]
    assert analyses.queueing_lock == "run_analyses"
    assert analyses.lock is None  # várias rodadas juntas, até investigation.max_parallel
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_analyses", "")].cron == ANALYSES_CRON == "* * * * * */5"
    assert app.tasks["reap_stuck_analyses"].lock == "reap_stuck_analyses"
    assert periodic[("reap_stuck_analyses", "")].cron == REAPER_CRON == "* * * * *"


async def test_analyses_job_drains_the_queue_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = [True, True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_analysis", run_next)
    app = registered(container)

    await app.tasks["run_analyses"].func(timestamp=0)

    assert len(calls) == 3
    assert calls[0] == {
        "queue": container.analysis_queue,
        "repository": container.repository,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "clock": container.clock,
        "policy": container.analysis,
    }


async def test_reaper_job_reaps_with_the_container_policy(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_analyses", reap)
    app = registered(container)

    await app.tasks["reap_stuck_analyses"].func(timestamp=0)

    assert calls == [
        (container.analysis_queue, container.repository, container.clock, container.analysis)
    ]
