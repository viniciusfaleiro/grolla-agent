"""Triagem do incidente novo (pedido, modelo, falha e reaper) e junção de incidentes."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.incidents import IntakePolicy, receive_alerts
from grolla.application.investigation import (
    STAGE_TRIAGE,
    TRIAGE_PROMPT,
    LanguageModelError,
    TokenBudget,
    TriageBrief,
    triage_text,
)
from grolla.application.triage import (
    TriagePolicy,
    merge_incidents,
    reap_stuck_triages,
    run_next_triage,
)
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentState,
    NotificationKind,
    Severity,
    SeverityPolicy,
    SeverityRule,
)
from grolla.domain.memory import (
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.domain.triage import TriageDraft
from grolla.domain.war_room import InvalidActionError
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import ScriptedLanguageModel
from grolla.testing.memory import InMemoryMemoryStore
from grolla.testing.triage import DONE, FAILED, InMemoryTriageQueue


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


INTAKE = IntakePolicy(
    label_filter={},
    severity=SeverityPolicy(
        rules=(SeverityRule({"severity": "critical"}, Severity.P1),), default=Severity.P3
    ),
    window=timedelta(minutes=15),
    page_url=page_url,
    triage=True,
)
POLICY = TriagePolicy(window=timedelta(minutes=30), timeout=timedelta(seconds=1))


def note(subject: str, body: str) -> Memory:
    return Memory(
        id=uuid4(),
        kind=MemoryKind.NOTE,
        subject=subject,
        subject_kind=SubjectKind.SERVICE,
        title=body,
        body=body,
        confidence=Confidence.CONFIRMED,
        source=Source(SourceKind.PERSON, "Ana"),
        created_at=T0,
        updated_at=T0,
    )


class World:
    def __init__(self, model: ScriptedLanguageModel | None = None) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryTriageQueue(self.repository)
        self.model = model or ScriptedLanguageModel()
        self.memory = InMemoryMemoryStore()
        self.clock = FakeClock(T0)

    async def alert(self, service: str, *, critical: bool = False) -> Incident:
        labels = {"service_name": service, **({"severity": "critical"} if critical else {})}
        alert = make_alert(service=service, labels=labels, rule_name=f"Erro em {service}")
        await receive_alerts([alert], self.repository, INTAKE, self.clock)
        (incident,) = [i for i in self.repository.incidents.values() if service in i.services]
        return incident

    async def triage(self) -> bool:
        return await run_next_triage(
            queue=self.queue,
            repository=self.repository,
            model=self.model,
            clock=self.clock,
            policy=POLICY,
            page_url=page_url,
            memory=self.memory,
        )

    def events(self, incident: Incident, kind: EventType) -> list[dict[str, object]]:
        return [
            dict(e.data)
            for e in self.repository.events
            if e.incident_id == incident.id and e.type is kind
        ]

    def cards(self, incident: Incident) -> list[NotificationKind]:
        return [
            o.notification.kind
            for o in self.repository.outbox
            if o.notification.incident_id == incident.id
        ]


# ---- Pedido ----


async def test_opening_an_incident_requests_the_triage_only_when_enabled() -> None:
    world = World()
    incident = await world.alert("pedidos-api")
    grouped = make_alert(service="pedidos-api", labels={"service_name": "pedidos-api"})
    world.clock.advance(timedelta(minutes=1))
    await receive_alerts([grouped], world.repository, INTAKE, world.clock)

    assert world.repository.triage_requests == {incident.id: T0}

    off = InMemoryIncidentRepository()
    alert = make_alert(service="captura-api")
    await receive_alerts([alert], off, replace(INTAKE, triage=False), FakeClock(T0))
    assert off.triage_requests == {}


# ---- Triagem ----


async def test_alone_and_classified_by_rule_finishes_without_the_model() -> None:
    world = World()
    incident = await world.alert("pedidos-api", critical=True)

    assert await world.triage()

    assert world.model.triage_briefs == []
    assert world.events(incident, EventType.TRIAGE_COMPLETED) == [
        {"same_as": None, "severity": None}
    ]
    assert world.queue.jobs[incident.id].status == DONE
    assert not await world.triage()


async def test_same_problem_on_another_service_suggests_the_merge() -> None:
    world = World()
    first = await world.alert("pedidos-api", critical=True)
    world.repository.analyses[first.id] = make_analysis(first.id)
    await world.memory.save([(note("pedidos-api", "usa grolla-lab-db"), None)])
    assert await world.triage()  # o primeiro, sozinho
    world.clock.advance(timedelta(minutes=2))
    world.model.triage_draft = TriageDraft(first.id, "os dois usam grolla-lab-db", None, "")
    second = await world.alert("captura-api", critical=True)

    assert await world.triage()

    (brief,) = world.model.triage_briefs
    assert brief.incident.id == second.id
    assert not brief.opened_by_default
    ((other, summary),) = brief.others
    assert other.id == first.id
    assert "Erro em pedidos-api" in summary
    assert make_analysis(first.id).summary in summary
    assert [m.body for m in brief.memory.notes] == ["usa grolla-lab-db"]
    assert world.events(second, EventType.MERGE_SUGGESTED) == [
        {"other": str(first.id), "reason": "os dois usam grolla-lab-db"}
    ]
    assert world.cards(second)[-1] is NotificationKind.MERGE_SUGGESTED
    (usage,) = [u for i, u, _ in world.repository.usages if i == second.id]
    assert usage.stage == STAGE_TRIAGE


async def test_default_severity_is_raised_by_the_suggestion() -> None:
    world = World(
        ScriptedLanguageModel(triage_draft=TriageDraft(None, "", Severity.P1, "autoriza"))
    )
    incident = await world.alert("autorizador-api")

    assert await world.triage()

    (brief,) = world.model.triage_briefs
    assert brief.opened_by_default
    raised = world.repository.incidents[incident.id]
    assert (raised.severity, raised.state) == (Severity.P1, IncidentState.DETECTED)
    assert world.events(incident, EventType.SEVERITY_CHANGED) == [
        {"from": "P3", "to": "P1", "reason": "sugerida pelo agente: autoriza"}
    ]
    assert NotificationKind.SEVERITY_RAISED in world.cards(incident)


async def test_alert_grouped_during_the_model_call_is_kept() -> None:
    class Grouping(ScriptedLanguageModel):
        async def triage(self, brief: TriageBrief, budget: TokenBudget) -> TriageDraft:
            again = make_alert(
                service="autorizador-api", labels={"service_name": "autorizador-api"}
            )
            await receive_alerts([again], world.repository, INTAKE, world.clock)
            return await super().triage(brief, budget)

    world = World(Grouping(triage_draft=TriageDraft(None, "", Severity.P2, "autoriza")))
    incident = await world.alert("autorizador-api")

    assert await world.triage()

    assert len(world.repository.incidents[incident.id].firing) == 2
    assert world.repository.incidents[incident.id].severity is Severity.P2


async def test_model_failure_finishes_with_the_reason_and_keeps_the_incident() -> None:
    world = World(ScriptedLanguageModel(triage_error=LanguageModelError("fora do ar")))
    incident = await world.alert("autorizador-api")

    assert await world.triage()

    assert world.repository.incidents[incident.id] == incident
    assert world.events(incident, EventType.TRIAGE_COMPLETED) == []
    job = world.queue.jobs[incident.id]
    assert job.status == FAILED
    assert job.reason


async def test_model_timeout_finishes_with_the_reason() -> None:
    class Slow(ScriptedLanguageModel):
        async def triage(self, brief: TriageBrief, budget: TokenBudget) -> TriageDraft:
            await asyncio.sleep(5)
            return await super().triage(brief, budget)

    world = World(Slow())
    incident = await world.alert("autorizador-api")
    fast = TriagePolicy(timeout=timedelta(milliseconds=10))

    assert await run_next_triage(
        queue=world.queue,
        repository=world.repository,
        model=world.model,
        clock=world.clock,
        policy=fast,
        page_url=page_url,
    )

    assert world.queue.jobs[incident.id].status == FAILED


async def test_closed_or_missing_incident_finishes_without_deciding() -> None:
    world = World()
    incident = await world.alert("autorizador-api")
    world.repository.incidents[incident.id] = replace(incident, state=IncidentState.DISCARDED)

    assert await world.triage()

    assert world.model.triage_briefs == []
    assert world.events(incident, EventType.TRIAGE_COMPLETED) == []
    assert world.queue.jobs[incident.id].status == DONE


async def test_reaper_fails_triages_stuck_past_the_timeout() -> None:
    world = World()
    incident = await world.alert("autorizador-api")
    assert await world.queue.claim(T0) is not None

    assert await reap_stuck_triages(world.queue, world.clock, POLICY) == 0
    world.clock.advance(timedelta(minutes=5))
    assert await reap_stuck_triages(world.queue, world.clock, POLICY) == 1

    assert world.queue.jobs[incident.id].status == FAILED


# ---- Juntar ----


async def merge(world: World, source: Incident, target: Incident, author: str = "Ana") -> Incident:
    return await merge_incidents(
        repository=world.repository,
        source_id=source.id,
        target_id=target.id,
        author=author,
        clock=world.clock,
        page_url=page_url,
    )


async def test_merge_saves_both_with_events_and_the_card() -> None:
    world = World()
    target = await world.alert("pedidos-api")
    source = await world.alert("captura-api", critical=True)

    merged = await merge(world, source, target)

    assert world.repository.incidents[target.id] == merged
    assert merged.services == ("pedidos-api", "captura-api")
    assert merged.severity is Severity.P1
    assert world.repository.incidents[source.id].state is IncidentState.DISCARDED
    assert world.events(source, EventType.MERGED_INTO) == [{"into": str(target.id)}]
    assert world.events(target, EventType.INCIDENT_MERGED) == [{"merged": str(source.id)}]
    assert world.cards(target)[-1] is NotificationKind.INCIDENT_MERGED


async def test_merge_refuses_and_writes_nothing() -> None:
    world = World()
    incident = await world.alert("pedidos-api")
    before = list(world.repository.events)

    with pytest.raises(InvalidActionError):
        await merge(world, incident, incident)
    with pytest.raises(IncidentNotFoundError):
        await merge(world, incident, make_incident())
    with pytest.raises(IncidentNotFoundError):
        await merge(world, make_incident(), incident)

    assert world.repository.events == before


# ---- Prompt ----


def test_triage_text_lists_the_new_incident_and_each_other_with_its_id_and_services() -> None:
    incident = make_incident(services=("captura-api",))
    other = make_incident(services=("pedidos-api",))
    alert = make_alert(service="captura-api", rule_name="Erro em captura")
    brief = TriageBrief(
        incident,
        (alert,),
        ((other, "Alertas: Erro em pedidos-api"),),
        MemoryContext(dossiers=(note("pedidos-api", "usa grolla-lab-db"),)),
        opened_by_default=True,
        now=T0,
    )

    text = triage_text(brief)

    header, others = text.split("Outros incidentes abertos", 1)
    assert str(incident.id) in header
    assert "padrão (nenhuma regra casou)" in header
    assert "Erro em captura" in header
    first = others.splitlines()[1]
    assert str(other.id) in first
    assert "pedidos-api" in first
    assert "Alertas: Erro em pedidos-api" in others
    assert "usa grolla-lab-db" in others
    assert "same_as" in TRIAGE_PROMPT
    assert "Na dúvida, não junte" in TRIAGE_PROMPT


def test_triage_text_without_alerts_or_others() -> None:
    brief = TriageBrief(make_incident(), (), (), MemoryContext(), opened_by_default=False, now=T0)

    text = triage_text(brief)

    assert "vinda da regra" in text
    assert text.count("- nenhum") == 2
