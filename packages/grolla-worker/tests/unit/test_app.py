from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import grolla_worker.app
import pytest
from grolla.adapters.postgres import PostgresDatabase
from grolla.application.clock import Clock
from grolla.application.console import ConsolePolicy
from grolla.application.follow_up import ImpactPolicy
from grolla.application.incidents import ChatNotifier, NotificationOutbox
from grolla.application.investigation import NoCloudInventory
from grolla.application.memory import STUDY_AUTHOR_SYSTEM, StudyMode
from grolla.application.memory_retrieval import RagPolicy
from grolla.application.triage import TriagePolicy
from grolla.bootstrap import TeamConfig, WorkerContainer, WorkerSettings
from grolla.bootstrap.team_config import MemoryConfig, StudyConfig
from grolla.domain.follow_up import FollowUpPolicy
from grolla.testing.clock import FakeClock
from grolla.testing.console import InMemoryConsoleStore
from grolla.testing.follow_up import InMemoryCycleQueue
from grolla.testing.incidents import (
    FakeChatNotifier,
    InMemoryAnalysisQueue,
    InMemoryChatQueue,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)
from grolla.testing.investigation import FakeChangeHistory, FakeTelemetry, ScriptedLanguageModel
from grolla.testing.memory import (
    FakeTextEmbedder,
    InMemoryLearningQueue,
    InMemoryMemoryStore,
    InMemoryStudyQueue,
)
from grolla.testing.post_mortem import InMemoryPostMortemStore
from grolla.testing.triage import InMemoryTriageQueue
from grolla_worker.app import (
    ANALYSES_CRON,
    ANALYSIS_QUEUE,
    CONSOLE_QUEUE,
    CONSOLE_REPLIES_CRON,
    CYCLES_CRON,
    DEFAULT_QUEUE,
    DELIVERY_CRON,
    INDEX_CRON,
    LEARNINGS_CRON,
    POST_MORTEMS_CRON,
    REAPER_CRON,
    REPLIES_CRON,
    SCHEDULE_CYCLES_CRON,
    STUDIES_CRON,
    TRIAGES_CRON,
    VERIFICATIONS_CRON,
    build_app,
)
from pydantic import SecretStr


def registered(container: WorkerContainer) -> Any:
    """O app montado, como Any: os tipos genéricos do procrastinate ficam parcialmente
    desconhecidos para o pyright strict."""
    return build_app(container)


DATABASE_URL = "postgresql://grolla:grolla@localhost:5433/grolla"  # nunca é conectado aqui


@pytest.fixture
def config() -> TeamConfig:
    return TeamConfig(team="t", page_base_url="http://grolla")


@pytest.fixture
async def container(config: TeamConfig) -> AsyncIterator[WorkerContainer]:
    database = PostgresDatabase(DATABASE_URL)
    repository = InMemoryIncidentRepository()
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
        analysis_queue=InMemoryAnalysisQueue(repository),
        chat_queue=InMemoryChatQueue(repository),
        model=ScriptedLanguageModel(),
        telemetry=FakeTelemetry(),
        changes=FakeChangeHistory(),
        cloud=NoCloudInventory(),
        analysis=config.analysis_policy(),
        memory=InMemoryMemoryStore(),
        learning_queue=InMemoryLearningQueue(repository),
        studies=InMemoryStudyQueue(),
        memory_policy=config.memory_policy(),
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
        "run_verifications": ANALYSIS_QUEUE,
        "reap_stuck_verifications": ANALYSIS_QUEUE,
        "run_replies": ANALYSIS_QUEUE,
        "reap_stuck_replies": ANALYSIS_QUEUE,
        "run_learnings": ANALYSIS_QUEUE,
        "reap_stuck_learnings": ANALYSIS_QUEUE,
        "run_studies": ANALYSIS_QUEUE,
        "reap_stuck_studies": ANALYSIS_QUEUE,
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
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.analysis,
        "memory": container.memory,
        "embedder": container.embedder,
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


def test_verifications_run_every_five_seconds_in_parallel_and_their_reaper_once_a_minute(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    verifications = app.tasks["run_verifications"]
    assert verifications.queueing_lock == "run_verifications"
    assert verifications.lock is None
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_verifications", "")].cron == VERIFICATIONS_CRON == "* * * * * */5"
    assert app.tasks["reap_stuck_verifications"].lock == "reap_stuck_verifications"
    assert periodic[("reap_stuck_verifications", "")].cron == REAPER_CRON


async def test_verifications_job_drains_the_queue_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_verification", run_next)
    app = registered(container)

    await app.tasks["run_verifications"].func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "queue": container.analysis_queue,
        "repository": container.repository,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.analysis,
        "memory": container.memory,
        "embedder": container.embedder,
    }


async def test_verification_reaper_reaps_with_the_container_policy(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_verifications", reap)
    app = registered(container)

    await app.tasks["reap_stuck_verifications"].func(timestamp=0)

    assert calls == [
        (container.analysis_queue, container.repository, container.clock, container.analysis)
    ]


def test_replies_run_every_two_seconds_in_parallel_and_their_reaper_once_a_minute(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    replies = app.tasks["run_replies"]
    assert replies.queueing_lock == "run_replies"
    assert replies.lock is None
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_replies", "")].cron == REPLIES_CRON == "* * * * * */2"
    assert app.tasks["reap_stuck_replies"].queueing_lock == "reap_stuck_replies"
    assert app.tasks["reap_stuck_replies"].lock == "reap_stuck_replies"
    assert periodic[("reap_stuck_replies", "")].cron == REAPER_CRON


async def test_replies_job_drains_the_chat_queue_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_reply", run_next)
    app = registered(container)

    await app.tasks["run_replies"].func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "queue": container.chat_queue,
        "repository": container.repository,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.analysis,
        "memory": container.memory,
        "embedder": container.embedder,
    }


async def test_reply_reaper_reaps_with_the_container_policy(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_replies", reap)
    app = registered(container)

    await app.tasks["reap_stuck_replies"].func(timestamp=0)

    assert calls == [
        (container.chat_queue, container.repository, container.clock, container.analysis)
    ]


def test_learnings_run_every_ten_seconds_and_their_reaper_once_a_minute(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    learnings = app.tasks["run_learnings"]
    assert learnings.queueing_lock == "run_learnings"
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_learnings", "")].cron == LEARNINGS_CRON == "* * * * * */10"
    assert app.tasks["reap_stuck_learnings"].lock == "reap_stuck_learnings"
    assert periodic[("reap_stuck_learnings", "")].cron == REAPER_CRON


async def test_learnings_job_drains_the_queue_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_learning", run_next)
    app = registered(container)

    await app.tasks["run_learnings"].func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "queue": container.learning_queue,
        "memory": container.memory,
        "repository": container.repository,
        "model": container.model,
        "clock": container.clock,
        "policy": container.memory_policy,
        "post_mortems": container.post_mortems,  # o post mortem aprovado ensina a memória
    }


async def test_reapers_of_the_memory_use_the_container_policy(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_learnings", reap)
    monkeypatch.setattr(grolla_worker.app, "reap_stuck_studies", reap)
    app = registered(container)

    await app.tasks["reap_stuck_learnings"].func(timestamp=0)
    await app.tasks["reap_stuck_studies"].func(timestamp=0)

    assert calls == [
        (container.learning_queue, container.clock, container.memory_policy),
        (container.studies, container.clock, container.memory_policy),
    ]


def test_studies_run_one_round_at_a_time_every_minute(container: WorkerContainer) -> None:
    app = registered(container)

    studies = app.tasks["run_studies"]
    assert studies.queueing_lock == "run_studies"
    assert studies.lock == "run_studies"
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_studies", "")].cron == STUDIES_CRON == "* * * * *"


async def test_studies_job_drains_the_rounds_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_study", run_next)
    app = registered(container)

    await app.tasks["run_studies"].func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "studies": container.studies,
        "memory": container.memory,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.memory_policy,
        "embedder": container.embedder,
    }


def test_memory_is_not_indexed_without_rag(container: WorkerContainer) -> None:
    app = registered(container)

    assert "index_memory" not in app.tasks


async def test_memory_index_runs_every_thirty_seconds_until_nothing_is_pending(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    rag = RagPolicy(batch=2)
    embedder = FakeTextEmbedder()
    container = replace(
        container, memory_policy=replace(container.memory_policy, rag=rag), embedder=embedder
    )
    indexed = [2, 2, 1]
    calls: list[dict[str, Any]] = []

    async def index(**ports: Any) -> int:
        calls.append(ports)
        return indexed.pop(0)

    monkeypatch.setattr(grolla_worker.app, "index_memory", index)
    app = registered(container)

    task = app.tasks["index_memory"]
    assert (task.queue, task.queueing_lock, task.lock) == (
        ANALYSIS_QUEUE,
        "index_memory",
        "index_memory",
    )
    assert app.periodic_registry.periodic_tasks[("index_memory", "")].cron == INDEX_CRON
    assert INDEX_CRON == "* * * * * */30"

    await task.func(timestamp=0)

    assert len(calls) == 3
    assert calls[0] == {
        "memory": container.memory,
        "embedder": embedder,
        "clock": container.clock,
        "policy": rag,
    }


def test_daily_study_is_not_scheduled_without_the_study_section(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    assert "schedule_daily_study" not in app.tasks


@pytest.mark.parametrize(
    "config",
    [
        TeamConfig(
            team="t",
            page_base_url="http://grolla",
            memory=MemoryConfig(study=StudyConfig(cron="30 5 * * *")),
        )
    ],
)
async def test_daily_study_is_scheduled_on_the_yaml_cron_and_asks_for_a_daily_round(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    task = app.tasks["schedule_daily_study"]
    assert task.queue == DEFAULT_QUEUE
    assert app.periodic_registry.periodic_tasks[("schedule_daily_study", "")].cron == "30 5 * * *"

    await task.func(timestamp=0)

    studies = container.studies
    assert isinstance(studies, InMemoryStudyQueue)
    assert [(r.mode, r.author) for r in studies.runs] == [(StudyMode.DAILY, STUDY_AUTHOR_SYSTEM)]


def test_cycles_are_not_scheduled_without_the_follow_up_section(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    assert {"schedule_cycles", "run_cycles", "reap_stuck_cycles"}.isdisjoint(app.tasks)


def following(container: WorkerContainer) -> WorkerContainer:
    repository = container.repository
    assert isinstance(repository, InMemoryIncidentRepository)
    return replace(
        container,
        cycle_queue=InMemoryCycleQueue(repository),
        follow_up_policy=FollowUpPolicy(),
        impact_policy=ImpactPolicy(per_minute={"pedidos-api": 1000}),
    )


async def test_cycles_are_scheduled_every_thirty_seconds_with_the_container_ports(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = following(container)
    calls: list[tuple[Any, ...]] = []

    async def schedule(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "schedule_cycles", schedule)
    app = registered(container)

    task = app.tasks["schedule_cycles"]
    assert (task.queue, task.queueing_lock) == (DEFAULT_QUEUE, "schedule_cycles")
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("schedule_cycles", "")].cron == SCHEDULE_CYCLES_CRON == "* * * * * */30"

    await task.func(timestamp=0)

    assert calls == [
        (
            container.cycle_queue,
            container.repository,
            container.clock,
            container.follow_up_policy,
            container.follow_up_limits,
        )
    ]


async def test_cycles_run_every_ten_seconds_one_round_at_a_time_until_none_is_pending(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = following(container)
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_cycle", run_next)
    app = registered(container)

    task = app.tasks["run_cycles"]
    assert (task.queue, task.queueing_lock, task.lock) == (
        ANALYSIS_QUEUE,
        "run_cycles",
        "run_cycles",
    )
    assert app.periodic_registry.periodic_tasks[("run_cycles", "")].cron == CYCLES_CRON
    assert CYCLES_CRON == "* * * * * */10"

    await task.func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "queue": container.cycle_queue,
        "repository": container.repository,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.follow_up_policy,
        "limits": container.follow_up_limits,
        "impact": container.impact_policy,
        "page_url": container.analysis.page_url,
        "memory": container.memory,
        "embedder": container.embedder,
    }


async def test_stuck_cycles_are_reaped_once_a_minute(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = following(container)
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_cycles", reap)
    app = registered(container)

    task = app.tasks["reap_stuck_cycles"]
    assert (task.queue, task.lock) == (ANALYSIS_QUEUE, "reap_stuck_cycles")
    assert app.periodic_registry.periodic_tasks[("reap_stuck_cycles", "")].cron == REAPER_CRON

    await task.func(timestamp=0)

    assert calls == [
        (
            container.cycle_queue,
            container.repository,
            container.clock,
            container.follow_up_policy,
            container.follow_up_limits,
        )
    ]


def test_post_mortems_are_not_scheduled_without_the_store(container: WorkerContainer) -> None:
    app = registered(container)

    assert {"run_post_mortems", "reap_stuck_post_mortems"}.isdisjoint(app.tasks)


def writing_post_mortems(container: WorkerContainer) -> WorkerContainer:
    repository = container.repository
    assert isinstance(repository, InMemoryIncidentRepository)
    return replace(container, post_mortems=InMemoryPostMortemStore(repository))


async def test_post_mortems_run_every_ten_seconds_one_round_at_a_time_until_none_is_pending(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = writing_post_mortems(container)
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_post_mortem", run_next)
    app = registered(container)

    task = app.tasks["run_post_mortems"]
    assert (task.queue, task.queueing_lock, task.lock) == (
        ANALYSIS_QUEUE,
        "run_post_mortems",
        "run_post_mortems",
    )
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_post_mortems", "")].cron == POST_MORTEMS_CRON == "* * * * * */10"

    await task.func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "store": container.post_mortems,
        "repository": container.repository,
        "model": container.model,
        "clock": container.clock,
        "policy": container.post_mortem_policy,
        "impact": container.impact_policy,
    }


async def test_stuck_post_mortems_are_reaped_once_a_minute(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = writing_post_mortems(container)
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_post_mortems", reap)
    app = registered(container)

    task = app.tasks["reap_stuck_post_mortems"]
    assert (task.queue, task.lock) == (ANALYSIS_QUEUE, "reap_stuck_post_mortems")
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("reap_stuck_post_mortems", "")].cron == REAPER_CRON

    await task.func(timestamp=0)

    assert calls == [
        (
            container.post_mortems,
            container.repository,
            container.clock,
            container.post_mortem_policy,
        )
    ]


def test_triages_are_not_scheduled_without_noise(container: WorkerContainer) -> None:
    app = registered(container)

    assert {"run_triages", "reap_stuck_triages"}.isdisjoint(app.tasks)


def triaging(container: WorkerContainer) -> WorkerContainer:
    repository = container.repository
    assert isinstance(repository, InMemoryIncidentRepository)
    return replace(
        container, triage_queue=InMemoryTriageQueue(repository), triage_policy=TriagePolicy()
    )


async def test_triages_run_every_five_seconds_one_round_at_a_time_until_none_is_pending(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = triaging(container)
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_triage", run_next)
    app = registered(container)

    task = app.tasks["run_triages"]
    assert (task.queue, task.queueing_lock, task.lock) == (
        ANALYSIS_QUEUE,
        "run_triages",
        "run_triages",
    )
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_triages", "")].cron == TRIAGES_CRON == "* * * * * */5"

    await task.func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "queue": container.triage_queue,
        "repository": container.repository,
        "model": container.model,
        "clock": container.clock,
        "policy": container.triage_policy,
        "page_url": container.analysis.page_url,
        "memory": container.memory,
    }


async def test_stuck_triages_are_reaped_once_a_minute(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = triaging(container)
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_triages", reap)
    app = registered(container)

    task = app.tasks["reap_stuck_triages"]
    assert (task.queue, task.lock) == (ANALYSIS_QUEUE, "reap_stuck_triages")
    assert app.periodic_registry.periodic_tasks[("reap_stuck_triages", "")].cron == REAPER_CRON

    await task.func(timestamp=0)

    assert calls == [(container.triage_queue, container.clock, container.triage_policy)]


def test_console_replies_are_not_scheduled_without_the_console(
    container: WorkerContainer,
) -> None:
    app = registered(container)

    assert {"run_console_replies", "reap_stuck_console_replies"}.isdisjoint(app.tasks)


def with_console(container: WorkerContainer) -> WorkerContainer:
    return replace(container, console=InMemoryConsoleStore(), console_policy=ConsolePolicy())


async def test_console_replies_run_every_three_seconds_in_their_own_queue(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = with_console(container)
    pending = [True, False]
    calls: list[dict[str, Any]] = []

    async def run_next(**ports: Any) -> bool:
        calls.append(ports)
        return pending.pop(0)

    monkeypatch.setattr(grolla_worker.app, "run_next_console_reply", run_next)
    app = registered(container)

    task = app.tasks["run_console_replies"]
    assert (task.queue, task.queueing_lock, task.lock) == (
        CONSOLE_QUEUE,
        "run_console_replies",
        "run_console_replies",
    )
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("run_console_replies", "")].cron == CONSOLE_REPLIES_CRON == "* * * * * */3"

    await task.func(timestamp=0)

    assert len(calls) == 2
    assert calls[0] == {
        "store": container.console,
        "model": container.model,
        "telemetry": container.telemetry,
        "changes": container.changes,
        "cloud": container.cloud,
        "clock": container.clock,
        "policy": container.console_policy,
        "memory": container.memory,
        "embedder": container.embedder,
    }


async def test_stuck_console_replies_are_reaped_once_a_minute(
    container: WorkerContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = with_console(container)
    calls: list[tuple[Any, ...]] = []

    async def reap(*args: Any) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(grolla_worker.app, "reap_stuck_console_replies", reap)
    app = registered(container)

    task = app.tasks["reap_stuck_console_replies"]
    assert (task.queue, task.lock) == (CONSOLE_QUEUE, "reap_stuck_console_replies")
    periodic = app.periodic_registry.periodic_tasks
    assert periodic[("reap_stuck_console_replies", "")].cron == REAPER_CRON

    await task.func(timestamp=0)

    assert calls == [(container.console, container.clock, container.console_policy)]
