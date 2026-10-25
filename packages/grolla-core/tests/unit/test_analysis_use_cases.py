"""Casos de uso da análise: todo pedido termina, concluído ou parcial, com o card de análise."""

from datetime import timedelta
from uuid import UUID

import pytest
from grolla.application.analysis import (
    NO_HYPOTHESIS,
    AnalysisPolicy,
    reap_stuck_analyses,
    run_next_analysis,
)
from grolla.application.investigation import (
    InvalidAnalysisError,
    LanguageModelError,
)
from grolla.domain.analysis import AnalysisStatus, PartialReason
from grolla.domain.incidents import EventType, NotificationKind, Severity
from grolla.testing.builders import T0, make_alert, make_draft, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryAnalysisQueue, InMemoryIncidentRepository
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeTelemetry,
    ScriptedLanguageModel,
    fake_usage,
)

SERVICE = "authorizer-api"
TELEMETRY_URL = "https://observabilidade.interno"
POLICY = AnalysisPolicy(
    timeout=timedelta(minutes=5),
    max_tokens=100_000,
    lookback=timedelta(minutes=60),
    page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
    telemetry_url=TELEMETRY_URL,
)
TOOLS = [
    ("query_metric", {"service": SERVICE, "signal": "taxa_de_erro"}),
    ("recent_deploys", {"service": SERVICE}),
]


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryAnalysisQueue(self.repository)
        self.clock = FakeClock(T0)
        self.telemetry = FakeTelemetry()
        self.changes = FakeChangeHistory({SERVICE: "1 deploy: 2.4.1 às 09:58 por ana"})

    async def open_incident(self) -> UUID:
        incident = make_incident(services=(SERVICE,), severity=Severity.P2)
        alert = make_alert(service=SERVICE)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.register_alert(alert)
            await uow.link_alert(alert, incident.id)
            await uow.request_analysis(incident.id, T0)
        return incident.id

    async def run(self, model: ScriptedLanguageModel, policy: AnalysisPolicy = POLICY) -> bool:
        return await run_next_analysis(
            queue=self.queue,
            repository=self.repository,
            model=model,
            telemetry=self.telemetry,
            changes=self.changes,
            clock=self.clock,
            policy=policy,
        )


@pytest.fixture
def world() -> World:
    return World()


async def test_nothing_to_do_without_requests(world: World) -> None:
    assert not await world.run(ScriptedLanguageModel())


async def test_completed_analysis_has_real_evaluations_card_and_usage(world: World) -> None:
    incident_id = await world.open_incident()
    model = ScriptedLanguageModel(make_draft(hypotheses=2), calls=TOOLS)

    assert await world.run(model)

    analysis = world.repository.analyses[incident_id]
    assert analysis.status is AnalysisStatus.COMPLETED
    assert [e.tool for e in analysis.evaluations] == ["query_metric", "recent_deploys"]
    assert len(analysis.hypotheses) == 2
    assert analysis.started_at == T0
    assert analysis.finished_at == T0

    [brief] = model.briefs
    assert brief.incident.id == incident_id
    assert [a.service for a in brief.alerts] == [SERVICE]

    events = await world.repository.events_of(incident_id)
    assert [e.type for e in events] == [
        EventType.ANALYSIS_COMPLETED,
        EventType.NOTIFICATION_QUEUED,
    ]
    assert events[0].data == {
        "status": "concluida",
        "reason": None,
        "summary": analysis.summary,
        "hypotheses": 2,
        "evaluations": 2,
    }

    [entry] = world.repository.outbox
    card = entry.notification
    assert card.kind is NotificationKind.ANALYSIS_READY
    assert card.analysis == analysis
    assert card.summary == analysis.summary
    assert card.evidence == analysis.hypotheses[0].title
    assert card.source_url == TELEMETRY_URL
    assert card.page_url == POLICY.page_url(incident_id)
    assert card.severity is Severity.P2
    assert card.services == (SERVICE,)

    assert [usage for _, usage, _ in world.repository.usages] == [fake_usage()] * 3
    assert not await world.run(model)


async def test_completed_analysis_without_hypotheses_says_so_on_the_card(world: World) -> None:
    await world.open_incident()

    await world.run(ScriptedLanguageModel(make_draft(hypotheses=0)))

    [entry] = world.repository.outbox
    assert entry.notification.evidence == NO_HYPOTHESIS


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LanguageModelError("API fora do ar"), PartialReason.LLM_FAILED),
        (InvalidAnalysisError("fora do schema"), PartialReason.INVALID_RESULT),
        (RuntimeError("inesperado"), PartialReason.LLM_FAILED),
        (TimeoutError("timeout do cliente HTTP"), PartialReason.LLM_FAILED),
    ],
)
async def test_failure_becomes_partial_keeping_the_evaluations(
    world: World, error: Exception, reason: PartialReason
) -> None:
    incident_id = await world.open_incident()

    assert await world.run(ScriptedLanguageModel(calls=TOOLS, error=error))

    analysis = world.repository.analyses[incident_id]
    assert analysis.status is AnalysisStatus.PARTIAL
    assert analysis.partial_reason is reason
    assert len(analysis.evaluations) == 2
    [completed, queued] = await world.repository.events_of(incident_id)
    assert completed.type is EventType.ANALYSIS_PARTIAL
    assert completed.data["status"] == "parcial"
    assert completed.data["reason"] == reason.value
    assert completed.data["hypotheses"] == 0
    assert queued.type is EventType.NOTIFICATION_QUEUED
    [entry] = world.repository.outbox
    assert entry.notification.analysis == analysis
    assert entry.notification.evidence.startswith("análise parcial: ")


async def test_slow_model_ends_as_partial_by_timeout(world: World) -> None:
    incident_id = await world.open_incident()
    policy = AnalysisPolicy(
        timeout=timedelta(milliseconds=10),
        max_tokens=POLICY.max_tokens,
        lookback=POLICY.lookback,
        page_url=POLICY.page_url,
        telemetry_url=None,
    )

    await world.run(ScriptedLanguageModel(delay=5), policy)

    analysis = world.repository.analyses[incident_id]
    assert analysis.partial_reason is PartialReason.TIMEOUT
    [entry] = world.repository.outbox
    assert entry.notification.source_url is None


async def test_exhausted_budget_ends_as_partial_with_the_usage_spent(world: World) -> None:
    incident_id = await world.open_incident()
    policy = AnalysisPolicy(
        timeout=POLICY.timeout,
        max_tokens=2_000,
        lookback=POLICY.lookback,
        page_url=POLICY.page_url,
        telemetry_url=TELEMETRY_URL,
    )

    await world.run(ScriptedLanguageModel(calls=TOOLS), policy)

    analysis = world.repository.analyses[incident_id]
    assert analysis.partial_reason is PartialReason.BUDGET
    assert len(analysis.evaluations) == 1  # o orçamento estourou antes da segunda ferramenta
    assert len(world.repository.usages) == 2


async def test_tool_failure_does_not_stop_the_analysis(world: World) -> None:
    incident_id = await world.open_incident()
    world.telemetry.failing = {"query_metric"}

    await world.run(ScriptedLanguageModel(calls=TOOLS))

    analysis = world.repository.analyses[incident_id]
    assert analysis.status is AnalysisStatus.COMPLETED
    assert [e.ok for e in analysis.evaluations] == [False, True]


async def test_stuck_analysis_is_reaped_as_interrupted_once(world: World) -> None:
    incident_id = await world.open_incident()
    await world.queue.claim(T0)

    world.clock.advance(POLICY.timeout + timedelta(minutes=1))
    assert await reap_stuck_analyses(world.queue, world.repository, world.clock, POLICY) == 0

    world.clock.advance(timedelta(minutes=2))
    assert await reap_stuck_analyses(world.queue, world.repository, world.clock, POLICY) == 1

    analysis = world.repository.analyses[incident_id]
    assert analysis.partial_reason is PartialReason.INTERRUPTED
    assert analysis.finished_at == world.clock.now()
    assert [e.type for e in await world.repository.events_of(incident_id)] == [
        EventType.ANALYSIS_PARTIAL,
        EventType.NOTIFICATION_QUEUED,
    ]
    [entry] = world.repository.outbox
    assert entry.notification.evidence == "análise parcial: interrompida"
    assert await reap_stuck_analyses(world.queue, world.repository, world.clock, POLICY) == 0


async def test_request_without_incident_is_an_error() -> None:
    world = World()
    async with world.repository.transaction() as uow:
        await uow.request_analysis(make_incident().id, T0)

    with pytest.raises(LookupError):
        await world.run(ScriptedLanguageModel())
