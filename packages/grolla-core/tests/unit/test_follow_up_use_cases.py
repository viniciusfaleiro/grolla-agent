"""Acompanhamento da crise: agendar, rodar e destravar os ciclos, e o impacto estimado."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.application.follow_up import (
    FollowUpLimits,
    ImpactPolicy,
    impact_of,
    impact_text,
    made_proposals,
    reap_stuck_cycles,
    run_next_cycle,
    schedule_cycles,
)
from grolla.application.investigation import (
    FOLLOW_UP_PROMPT,
    STAGE_FOLLOW_UP,
    InvalidAnalysisError,
    LanguageModelError,
    NoCloudInventory,
    follow_up_text,
)
from grolla.domain.analysis import AnalysisStatus, Evidence, PartialReason
from grolla.domain.follow_up import (
    BUDGET_REACHED,
    Cycle,
    CycleDraft,
    CycleStatus,
    FollowUpPolicy,
    MadeProposal,
    Proposal,
    Trend,
)
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.follow_up import InMemoryCycleQueue
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeTelemetry,
    ScriptedLanguageModel,
    fake_usage,
)
from grolla.testing.memory import FakeTextEmbedder, InMemoryMemoryStore

SERVICE = "pedidos-api"
POLICY = FollowUpPolicy()
LIMITS = FollowUpLimits(timeout=timedelta(seconds=1), max_tokens=50_000)
IMPACT = ImpactPolicy(per_minute={SERVICE: 1000})
TOOLS = [("query_metric", {"service": SERVICE, "signal": "taxa_de_erro"})]
ANALYSIS_DONE = T0 + timedelta(minutes=1)


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryCycleQueue(self.repository)
        self.clock = FakeClock(T0)
        self.telemetry = FakeTelemetry()

    async def open(
        self,
        severity: Severity = Severity.P1,
        state: IncidentState = IncidentState.HANDLING,
    ) -> Incident:
        """Incidente com a análise inicial concluída em ANALYSIS_DONE."""
        incident = make_incident(services=(SERVICE,), severity=severity, state=state)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.link_alert(make_alert(service=SERVICE), incident.id)
        self.repository.analyses[incident.id] = make_analysis(
            incident.id, status=AnalysisStatus.COMPLETED, finished_at=ANALYSIS_DONE
        )
        return incident

    def at(self, minutes: float) -> None:
        self.clock.advance(T0 + timedelta(minutes=minutes) - self.clock.now())

    async def schedule(self, limits: FollowUpLimits = LIMITS) -> int:
        return await schedule_cycles(self.queue, self.repository, self.clock, POLICY, limits)

    async def run(
        self, model: ScriptedLanguageModel, *, memory: InMemoryMemoryStore | None = None
    ) -> bool:
        return await run_next_cycle(
            queue=self.queue,
            repository=self.repository,
            model=model,
            telemetry=self.telemetry,
            changes=FakeChangeHistory(),
            cloud=NoCloudInventory(),
            clock=self.clock,
            policy=POLICY,
            limits=LIMITS,
            impact=IMPACT,
            page_url=page_url,
            memory=memory,
            embedder=FakeTextEmbedder() if memory else None,
        )

    async def cycle_at(self, minutes: float, model: ScriptedLanguageModel) -> None:
        self.at(minutes)
        assert await self.schedule() == 1
        assert await self.run(model)

    def events(self, incident: Incident, kind: EventType) -> list[IncidentEvent]:
        return [
            e for e in self.repository.events if e.incident_id == incident.id and e.type is kind
        ]


@pytest.fixture
def world() -> World:
    return World()


def stable(rate: float = 12.0, *, changed: bool = False) -> ScriptedLanguageModel:
    draft = CycleDraft(Trend.STABLE, rate, f"Erro estável em {rate:g}%.", changed)
    return ScriptedLanguageModel(follow_up_draft=draft, follow_up_calls=TOOLS)


# ---- schedule_cycles ----


async def test_due_cycle_is_enqueued_with_the_next_number(world: World) -> None:
    incident = await world.open()

    world.at(5)
    assert await world.schedule() == 0
    world.at(6)
    assert await world.schedule() == 1
    assert await world.schedule() == 0  # já pendente

    assert await world.repository.cycles_of(incident.id) == [
        Cycle(incident.id, 1, CycleStatus.PENDING, T0 + timedelta(minutes=6))
    ]


class LosingQueue(InMemoryCycleQueue):
    """Outro worker enfileirou o mesmo ciclo antes."""

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        await super().enqueue(incident_id, number, at)  # o outro worker
        return await super().enqueue(incident_id, number, at)


async def test_cycle_enqueued_by_another_worker_is_not_counted(world: World) -> None:
    await world.open()
    world.at(6)

    enqueued = await schedule_cycles(
        LosingQueue(world.repository), world.repository, world.clock, POLICY, LIMITS
    )

    assert enqueued == 0


async def test_p3_is_not_followed(world: World) -> None:
    await world.open(Severity.P3, IncidentState.INFORMED)

    world.at(60)

    assert await world.schedule() == 0
    assert world.repository.cycles == {}


async def test_incident_over_its_token_budget_stops_once(world: World) -> None:
    incident = await world.open()
    world.repository.usages.append((incident.id, fake_usage(600_000, STAGE_FOLLOW_UP), T0))

    world.at(6)
    assert await world.schedule() == 0
    world.at(7)
    assert await world.schedule() == 0

    stopped = world.events(incident, EventType.FOLLOW_UP_STOPPED)
    assert [e.data for e in stopped] == [{"reason": BUDGET_REACHED, "tokens": 660_000}]
    assert world.repository.cycles == {}


# ---- run_next_cycle ----


async def test_nothing_to_run_without_a_pending_cycle(world: World) -> None:
    assert not await world.run(ScriptedLanguageModel())


async def test_cycle_runs_with_the_chat_context_and_posts_the_update(world: World) -> None:
    incident = await world.open()
    async with world.repository.transaction() as uow:
        await uow.request_cycle(incident.id, T0 + timedelta(minutes=5))
    known = "https://observabilidade.interno/consulta"
    draft = CycleDraft(
        Trend.WORSE, 50.0, "Erro subiu para 50%.", True, (Evidence("erro em 50%", known),)
    )
    model = ScriptedLanguageModel(follow_up_draft=draft, follow_up_calls=TOOLS)

    await world.cycle_at(6, model)

    [cycle] = await world.repository.cycles_of(incident.id)
    assert cycle.status is CycleStatus.COMPLETED
    assert (cycle.trend, cycle.error_rate, cycle.posted) == (Trend.WORSE, 50.0, True)
    assert cycle.evidence == (Evidence("erro em 50%", known),)
    assert [e.tool for e in cycle.evaluations] == ["query_metric"]
    [(context, previous)] = model.follow_ups
    assert previous is None
    assert f"Serviços: {SERVICE}" in context.splitlines()
    assert "Análise inicial (concluida)" in context
    [entry] = world.repository.outbox
    card = entry.notification
    assert card.kind is NotificationKind.FOLLOW_UP_UPDATE
    assert card.cycle == cycle
    assert card.impact == "R$ 3.000,00 estimado"  # 1000 x 50% x 6 min desde a detecção
    assert card.page_url == page_url(incident.id)
    assert [e.type for e in world.repository.events] == [
        EventType.CYCLE_COMPLETED,
        EventType.NOTIFICATION_QUEUED,
    ]
    usages = [u for _, u, _ in world.repository.usages]
    assert {u.stage for u in usages} == {STAGE_FOLLOW_UP}
    assert world.repository.cycle_requests == {}  # o pedido antecipado foi atendido


async def test_next_cycle_gets_the_previous_one_and_the_memory(world: World) -> None:
    incident = await world.open()
    memory = InMemoryMemoryStore()
    await world.cycle_at(6, stable())
    model = stable()

    world.at(11)
    await world.schedule()
    assert await world.run(model, memory=memory)

    [(_, previous)] = model.follow_ups
    assert previous is not None
    assert previous.number == 1
    first, second = await world.repository.cycles_of(incident.id)
    assert first.posted
    assert not second.posted  # mesma tendência, sem mudança e antes de 15 min
    assert len(world.repository.outbox) == 1


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LanguageModelError("fora do ar"), PartialReason.LLM_FAILED),
        (InvalidAnalysisError("sem tendência"), PartialReason.INVALID_RESULT),
    ],
)
async def test_model_failure_becomes_a_partial_cycle(
    world: World, error: Exception, reason: PartialReason
) -> None:
    incident = await world.open()
    model = ScriptedLanguageModel(follow_up_error=error, follow_up_calls=TOOLS)

    await world.cycle_at(6, model)

    [cycle] = await world.repository.cycles_of(incident.id)
    assert (cycle.status, cycle.partial_reason) == (CycleStatus.PARTIAL, reason)
    assert [e.tool for e in cycle.evaluations] == ["query_metric"]
    assert [e.type for e in world.repository.events] == [EventType.CYCLE_PARTIAL]
    assert world.repository.outbox == []
    assert world.repository.incidents[incident.id] == incident  # o incidente segue


async def test_slow_model_ends_the_cycle_by_timeout(world: World) -> None:
    incident = await world.open()

    await world.cycle_at(6, ScriptedLanguageModel(follow_up_delay=5))

    [cycle] = await world.repository.cycles_of(incident.id)
    assert cycle.partial_reason is PartialReason.TIMEOUT


async def test_normal_for_fifteen_minutes_proposes_to_mitigate_once(world: World) -> None:
    incident = await world.open()
    normal = ScriptedLanguageModel()  # padrão: normalizado, 0% de erro
    for minutes in (6, 11, 16, 21, 26):
        await world.cycle_at(minutes, normal)

    proposals = world.events(incident, EventType.AGENT_PROPOSAL)
    assert [e.data["proposal"] for e in proposals] == ["mitigar"]
    assert proposals[0].at == T0 + timedelta(minutes=21)
    cards = [e.notification.kind for e in world.repository.outbox]
    assert cards.count(NotificationKind.AGENT_PROPOSAL) == 1
    assert world.repository.incidents[incident.id].state is IncidentState.HANDLING


async def test_proposals_made_come_from_the_events() -> None:
    incident = make_incident()
    data = {"proposal": "encerrar", "state": "mitigado", "reason": "x"}
    events = [
        IncidentEvent(incident.id, T0, EventType.AGENT_PROPOSAL, data),
        IncidentEvent(incident.id, T0, EventType.CYCLE_COMPLETED, {}),
    ]

    assert made_proposals(events) == [MadeProposal(Proposal.RESOLVE, IncidentState.MITIGATED)]


async def test_cycle_of_a_missing_incident_is_an_error(world: World) -> None:
    incident = await world.open()
    world.at(6)
    await world.schedule()
    del world.repository.incidents[incident.id]

    with pytest.raises(LookupError):
        await world.run(ScriptedLanguageModel())


# ---- reap_stuck_cycles ----


async def test_stuck_cycle_ends_as_interrupted(world: World) -> None:
    incident = await world.open()
    world.at(6)
    await world.schedule()
    await world.queue.claim(world.clock.now())

    world.at(8)
    assert await reap_stuck_cycles(world.queue, world.repository, world.clock, POLICY, LIMITS) == 0
    world.at(9)
    assert await reap_stuck_cycles(world.queue, world.repository, world.clock, POLICY, LIMITS) == 1

    [cycle] = await world.repository.cycles_of(incident.id)
    assert (cycle.status, cycle.partial_reason) == (CycleStatus.PARTIAL, PartialReason.INTERRUPTED)
    assert [e.type for e in world.repository.events] == [EventType.CYCLE_PARTIAL]


async def test_stuck_cycle_of_a_missing_incident_is_an_error(world: World) -> None:
    incident = await world.open()
    world.at(6)
    await world.schedule()
    await world.queue.claim(world.clock.now())
    del world.repository.incidents[incident.id]
    world.at(60)

    with pytest.raises(LookupError):
        await reap_stuck_cycles(world.queue, world.repository, world.clock, POLICY, LIMITS)


# ---- impacto ----


@pytest.mark.parametrize(
    ("value", "currency", "text"),
    [
        (3000, "BRL", "R$ 3.000,00 estimado"),
        (1234567.891, "BRL", "R$ 1.234.567,89 estimado"),
        (0, "BRL", "R$ 0,00 estimado"),
        (3000, "USD", "USD 3.000,00 estimado"),
    ],
)
def test_impact_text(value: float, currency: str, text: str) -> None:
    assert impact_text(value, currency) == text


async def test_impact_of_the_page_sums_the_cycles(world: World) -> None:
    incident = await world.open()
    for number, rate in ((1, 50.0), (2, 10.0)):
        world.repository.cycles[(incident.id, number)] = Cycle(
            incident.id,
            number,
            CycleStatus.COMPLETED,
            T0,
            finished_at=T0 + timedelta(minutes=5 * number),
            trend=Trend.STABLE,
            error_rate=rate,
        )

    assert await impact_of(world.repository, incident, IMPACT) == "R$ 3.000,00 estimado"
    other = replace(incident, services=("outro-api",))
    assert await impact_of(world.repository, other, IMPACT) is None


# ---- prompt ----


def test_follow_up_text_has_the_context_and_the_previous_cycle() -> None:
    previous = Cycle(
        UUID(int=1),
        1,
        CycleStatus.COMPLETED,
        T0,
        finished_at=T0 + timedelta(minutes=6),
        trend=Trend.STABLE,
        error_rate=12.5,
        summary="Erro estável em 12,5%.",
    )

    first = follow_up_text("CONTEXTO", None)
    later = follow_up_text("CONTEXTO", previous)

    assert first.startswith("CONTEXTO")
    assert "Ciclo anterior: nenhum" in first
    assert "Ciclo anterior (10:06): estavel, taxa de erro 12.5%. Erro estável em 12,5%." in later
    unmeasured = follow_up_text("C", replace(previous, error_rate=None))
    assert "taxa de erro não medida" in unmeasured
    assert "query_metric" in FOLLOW_UP_PROMPT
    assert "taxa_de_erro" in FOLLOW_UP_PROMPT
