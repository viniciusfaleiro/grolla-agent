"""Casos de uso da validação: toda validação termina, concluída ou parcial, com o card da causa
raiz.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from grolla.application.analysis import (
    NO_ROOT_CAUSE,
    AnalysisPolicy,
    reap_stuck_verifications,
    run_next_analysis,
    run_next_verification,
)
from grolla.application.investigation import (
    STAGE_VERIFICATION,
    BudgetExceededError,
    InvalidAnalysisError,
    LanguageModelError,
)
from grolla.domain.analysis import Action, AnalysisStatus, PartialReason
from grolla.domain.incidents import EventType, NotificationKind, Severity
from grolla.testing.builders import T0, make_alert, make_incident, make_verification_draft
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryAnalysisQueue, InMemoryIncidentRepository
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeCloudInventory,
    FakeTelemetry,
    ScriptedLanguageModel,
)

SERVICE = "pedidos-api"
TELEMETRY_URL = "https://observabilidade.interno"
POLICY = AnalysisPolicy(
    timeout=timedelta(minutes=5),
    max_tokens=100_000,
    lookback=timedelta(minutes=60),
    page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
    telemetry_url=TELEMETRY_URL,
    verification_timeout=timedelta(minutes=5),
    verification_max_tokens=100_000,
)
CLOUD_CALLS = [
    ("cloud_resources", {"service": SERVICE}),
    ("cloud_changes", {"service": SERVICE}),
]


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryAnalysisQueue(self.repository)
        self.clock = FakeClock(T0)
        self.telemetry = FakeTelemetry()
        self.changes = FakeChangeHistory({SERVICE: "1 deploy: 2.4.1 às 09:58 por ana"})
        self.cloud = FakeCloudInventory(resources={SERVICE: "banco pedidos-db: stopped"})

    async def analysed_incident(self) -> UUID:
        """Incidente cuja análise inicial terminou com duas hipóteses: validação pendente."""
        incident = make_incident(services=(SERVICE,), severity=Severity.P1)
        alert = make_alert(service=SERVICE)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.register_alert(alert)
            await uow.link_alert(alert, incident.id)
            await uow.request_analysis(incident.id, T0)
        await self._analyse(ScriptedLanguageModel())
        self.repository.outbox.clear()
        self.repository.events.clear()
        return incident.id

    async def _analyse(self, model: ScriptedLanguageModel) -> None:
        assert await run_next_analysis(
            queue=self.queue,
            repository=self.repository,
            model=model,
            telemetry=self.telemetry,
            changes=self.changes,
            clock=self.clock,
            policy=POLICY,
            cloud=self.cloud,
        )

    async def verify(self, model: ScriptedLanguageModel, policy: AnalysisPolicy = POLICY) -> bool:
        return await run_next_verification(
            queue=self.queue,
            repository=self.repository,
            model=model,
            telemetry=self.telemetry,
            changes=self.changes,
            clock=self.clock,
            policy=policy,
            cloud=self.cloud,
        )


@pytest.fixture
def world() -> World:
    return World()


async def test_nothing_to_do_without_verifications(world: World) -> None:
    assert not await world.verify(ScriptedLanguageModel())


async def test_confirmed_root_cause_has_plan_card_events_and_usage(world: World) -> None:
    incident_id = await world.analysed_incident()
    world.clock.advance(timedelta(minutes=1))
    model = ScriptedLanguageModel(verification_calls=CLOUD_CALLS)

    assert await world.verify(model)

    verification = world.repository.verifications[incident_id]
    assert verification.status is AnalysisStatus.COMPLETED
    assert verification.root_cause == make_verification_draft().root_cause
    assert [e.tool for e in verification.evaluations] == ["cloud_resources", "cloud_changes"]
    assert verification.started_at == world.clock.now()
    assert verification.finished_at == world.clock.now()
    [analysis] = model.verified_analyses
    assert analysis == world.repository.analyses[incident_id]
    assert world.cloud.calls == [("resources", SERVICE), ("changes", SERVICE)]

    [completed, queued] = await world.repository.events_of(incident_id)
    assert completed.type is EventType.VERIFICATION_COMPLETED
    assert completed.data == {
        "status": "concluida",
        "reason": None,
        "root_cause": verification.root_cause,
        "verdicts": 2,
        "actions": 2,
        "evaluations": 2,
    }
    assert queued.type is EventType.NOTIFICATION_QUEUED
    assert queued.data == {"kind": "root_cause"}

    [entry] = world.repository.outbox
    card = entry.notification
    assert card.kind is NotificationKind.ROOT_CAUSE
    assert card.verification == verification
    assert card.analysis is None
    assert card.summary == verification.root_cause
    assert card.evidence == verification.actions[0].action
    assert card.severity is Severity.P1
    assert card.source_url == TELEMETRY_URL
    assert card.page_url == POLICY.page_url(incident_id)

    stages = {usage.stage for owner, usage, _ in world.repository.usages if owner == incident_id}
    assert STAGE_VERIFICATION in stages
    assert not await world.verify(model)


async def test_nothing_confirmed_is_completed_and_says_what_was_missing(world: World) -> None:
    incident_id = await world.analysed_incident()
    draft = make_verification_draft(confirmed=False)

    await world.verify(ScriptedLanguageModel(verification=draft))

    verification = world.repository.verifications[incident_id]
    assert verification.status is AnalysisStatus.COMPLETED
    assert verification.actions == ()
    [entry] = world.repository.outbox
    assert entry.notification.summary == NO_ROOT_CAUSE
    assert entry.notification.evidence == draft.missing


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LanguageModelError("API fora do ar"), PartialReason.LLM_FAILED),
        (InvalidAnalysisError("fora do schema"), PartialReason.INVALID_RESULT),
        (BudgetExceededError("estourou"), PartialReason.BUDGET),
        (RuntimeError("inesperado"), PartialReason.LLM_FAILED),
    ],
)
async def test_failure_becomes_partial_keeping_the_evaluations(
    world: World, error: Exception, reason: PartialReason
) -> None:
    incident_id = await world.analysed_incident()
    model = ScriptedLanguageModel(verification_calls=CLOUD_CALLS, verification_error=error)

    assert await world.verify(model)

    verification = world.repository.verifications[incident_id]
    assert verification.status is AnalysisStatus.PARTIAL
    assert verification.partial_reason is reason
    assert len(verification.evaluations) == 2
    [partial, queued] = await world.repository.events_of(incident_id)
    assert partial.type is EventType.VERIFICATION_PARTIAL
    assert partial.data["reason"] == reason.value
    assert queued.type is EventType.NOTIFICATION_QUEUED
    [entry] = world.repository.outbox
    assert entry.notification.summary == NO_ROOT_CAUSE
    assert entry.notification.evidence == verification.missing
    assert entry.notification.evidence.startswith("Validação parcial: ")


async def test_plan_with_an_investigation_step_is_an_invalid_result(world: World) -> None:
    incident_id = await world.analysed_incident()
    draft = replace(
        make_verification_draft(),
        actions=(Action("Verificar a saúde do banco", "pedidos-db", "saber se caiu", None),),
    )

    await world.verify(ScriptedLanguageModel(verification=draft))

    verification = world.repository.verifications[incident_id]
    assert verification.partial_reason is PartialReason.INVALID_RESULT


async def test_slow_verification_ends_as_partial_by_its_own_timeout(world: World) -> None:
    incident_id = await world.analysed_incident()
    policy = replace(POLICY, verification_timeout=timedelta(milliseconds=10))

    await world.verify(ScriptedLanguageModel(verification_delay=5), policy)

    verification = world.repository.verifications[incident_id]
    assert verification.partial_reason is PartialReason.TIMEOUT


async def test_verification_has_its_own_token_budget(world: World) -> None:
    incident_id = await world.analysed_incident()
    policy = replace(POLICY, verification_max_tokens=2_000)

    await world.verify(ScriptedLanguageModel(verification_calls=CLOUD_CALLS), policy)

    verification = world.repository.verifications[incident_id]
    assert verification.partial_reason is PartialReason.BUDGET
    assert len(verification.evaluations) == 1


async def test_stuck_verification_is_reaped_as_interrupted_once(world: World) -> None:
    incident_id = await world.analysed_incident()
    await world.queue.claim_verification(world.clock.now())

    world.clock.advance(POLICY.verification_timeout + timedelta(minutes=1))
    assert await reap_stuck_verifications(world.queue, world.repository, world.clock, POLICY) == 0

    world.clock.advance(timedelta(minutes=2))
    assert await reap_stuck_verifications(world.queue, world.repository, world.clock, POLICY) == 1

    verification = world.repository.verifications[incident_id]
    assert verification.partial_reason is PartialReason.INTERRUPTED
    assert verification.finished_at == world.clock.now()
    assert [e.type for e in await world.repository.events_of(incident_id)] == [
        EventType.VERIFICATION_PARTIAL,
        EventType.NOTIFICATION_QUEUED,
    ]
    [entry] = world.repository.outbox
    assert entry.notification.evidence == "Validação parcial: interrompida."
    assert await reap_stuck_verifications(world.queue, world.repository, world.clock, POLICY) == 0


async def test_verification_without_initial_analysis_is_an_error(world: World) -> None:
    incident_id = await world.analysed_incident()
    del world.repository.analyses[incident_id]

    with pytest.raises(LookupError):
        await world.verify(ScriptedLanguageModel())
