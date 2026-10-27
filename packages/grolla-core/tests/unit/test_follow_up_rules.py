from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.domain.analysis import Evaluation, Evidence, PartialReason
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.follow_up import (
    LIMIT_REACHED,
    Cycle,
    CycleDecision,
    CycleDraft,
    CycleStatus,
    FollowUpPolicy,
    MadeProposal,
    Proposal,
    Trend,
    completed,
    cycle_due,
    decide_cycle,
    error_rate_text,
    estimated_impact,
    partial_cycle,
)
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.testing.builders import T0, make_incident

POLICY = FollowUpPolicy()
ANALYSIS_DONE = T0 + timedelta(minutes=1)
IMPACT = "R$ 1.000,00 estimado"
SOME_INCIDENT = UUID(int=1)


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


def p1(state: IncidentState = IncidentState.HANDLING) -> Incident:
    return make_incident(severity=Severity.P1, state=state)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def cycle(  # noqa: PLR0913 — cada campo do ciclo é ajustável no teste
    number: int,
    minutes: float,
    trend: Trend | None = Trend.STABLE,
    *,
    rate: float | None = 12.0,
    changed: bool = False,
    posted: bool = False,
    status: CycleStatus = CycleStatus.COMPLETED,
    incident_id: UUID = SOME_INCIDENT,
) -> Cycle:
    """Ciclo `number` que começou e terminou em `minutes` depois de T0."""
    return Cycle(
        incident_id,
        number,
        status,
        at(minutes),
        started_at=at(minutes),
        finished_at=None if status in (CycleStatus.PENDING, CycleStatus.RUNNING) else at(minutes),
        trend=trend,
        error_rate=rate,
        summary=f"ciclo {number}",
        changed=changed,
        posted=posted,
    )


def due(
    incident: Incident,
    cycles: tuple[Cycle, ...] = (),
    *,
    now: float,
    requested: float | None = None,
    analysis_done_at: datetime | None = ANALYSIS_DONE,
) -> bool:
    return cycle_due(
        incident,
        analysis_done_at=analysis_done_at,
        cycles=cycles,
        requested_at=None if requested is None else at(requested),
        now=at(now),
        policy=POLICY,
    )


def decide(
    incident: Incident,
    current: Cycle,
    history: tuple[Cycle, ...] = (),
    proposals: tuple[MadeProposal, ...] = (),
    policy: FollowUpPolicy = POLICY,
) -> CycleDecision:
    return decide_cycle(
        incident,
        cycle=current,
        history=history,
        proposals=proposals,
        impact=IMPACT,
        now=current.finished_at or current.requested_at,
        policy=policy,
        page_url=page_url,
    )


def types(decision: CycleDecision) -> list[EventType]:
    return [e.type for e in decision.events]


# ---- cycle_due ----


def test_first_cycle_is_due_one_interval_after_the_initial_analysis() -> None:
    assert not due(p1(), now=5.9)
    assert due(p1(), now=6)


@pytest.mark.parametrize(
    ("incident", "analysis_done_at"),
    [
        (make_incident(severity=Severity.P3, state=IncidentState.INFORMED), ANALYSIS_DONE),
        (p1(IncidentState.RESOLVED), ANALYSIS_DONE),
        (p1(IncidentState.DISCARDED), ANALYSIS_DONE),
        (p1(), None),
    ],
    ids=["p3", "resolvido", "descartado", "analise-em-andamento"],
)
def test_no_cycle_for_untracked_closed_or_still_analyzing_incidents(
    incident: Incident, analysis_done_at: datetime | None
) -> None:
    assert not due(incident, now=60, requested=59, analysis_done_at=analysis_done_at)


@pytest.mark.parametrize("status", [CycleStatus.PENDING, CycleStatus.RUNNING])
def test_no_new_cycle_while_one_is_pending_or_running(status: CycleStatus) -> None:
    assert not due(p1(), (cycle(1, 6, status=status),), now=30, requested=29)


def test_no_cycle_after_the_limit() -> None:
    policy = replace(POLICY, max_cycles=2)
    cycles = (cycle(1, 6), cycle(2, 11))

    assert not cycle_due(
        p1(),
        analysis_done_at=ANALYSIS_DONE,
        cycles=cycles,
        requested_at=None,
        now=at(60),
        policy=policy,
    )


def test_next_cycle_is_due_one_interval_after_the_last_started() -> None:
    cycles = (cycle(1, 6, status=CycleStatus.PARTIAL),)

    assert not due(p1(), cycles, now=10.9)
    assert due(p1(), cycles, now=11)


def test_request_brings_the_cycle_forward_if_the_last_started_over_a_minute_ago() -> None:
    cycles = (cycle(1, 6),)

    assert due(p1(), cycles, now=9, requested=8.5)
    assert not due(p1(), cycles, now=6.5, requested=6.4)


def test_p1_handled_for_twelve_minutes_had_two_cycles() -> None:
    incident = p1()
    cycles: list[Cycle] = []
    for second in range(0, 12 * 60 + 1, 30):
        if due(incident, tuple(cycles), now=second / 60):
            cycles.append(cycle(len(cycles) + 1, second / 60))

    assert [c.started_at for c in cycles] == [at(6), at(11)]


# ---- completed e partial_cycle ----


def test_completed_cycle_keeps_only_links_from_evaluations_and_three_evidences() -> None:
    known = "https://origem.interno/metrica"
    evaluations = (Evaluation("query_metric", "taxa_de_erro", "2%", known, ok=True),)
    draft = CycleDraft(
        Trend.BETTER,
        2.0,
        "Erro caiu para 2%.",
        changed=True,
        evidence=(
            Evidence("taxa em 2%", known),
            Evidence("inventado", "https://inventado.interno"),
            Evidence("sem link", None),
            Evidence("quarta", None),
        ),
    )
    job = cycle(1, 6, None, rate=None, status=CycleStatus.RUNNING)

    done = completed(job, draft, evaluations, at(7))

    assert done == replace(
        job,
        status=CycleStatus.COMPLETED,
        finished_at=at(7),
        trend=Trend.BETTER,
        error_rate=2.0,
        summary="Erro caiu para 2%.",
        changed=True,
        evidence=(
            Evidence("taxa em 2%", known),
            Evidence("inventado", None),
            Evidence("sem link", None),
        ),
        evaluations=evaluations,
    )


def test_partial_cycle_keeps_the_reason_and_what_was_evaluated() -> None:
    evaluations = (Evaluation("query_metric", "taxa_de_erro", "não foi possível", None, ok=False),)
    job = cycle(1, 6, None, rate=None, status=CycleStatus.RUNNING)

    partial = partial_cycle(job, PartialReason.TIMEOUT, evaluations, at(9))

    assert partial.status is CycleStatus.PARTIAL
    assert partial.finished_at == at(9)
    assert partial.partial_reason is PartialReason.TIMEOUT
    assert partial.summary == "Ciclo parcial: tempo esgotado."
    assert partial.evaluations == evaluations
    assert partial.trend is None


# ---- decide_cycle: o card só quando muda ----


def test_first_cycle_posts_the_update_card() -> None:
    incident = p1()
    evidence = Evidence("taxa em 12%", "https://origem.interno/metrica")
    current = replace(cycle(1, 6, incident_id=incident.id), evidence=(evidence,))

    decision = decide(incident, current)

    assert decision.cycle == replace(current, posted=True)
    [card] = decision.notifications
    assert card.kind is NotificationKind.FOLLOW_UP_UPDATE
    assert card.cycle == decision.cycle
    assert card.impact == IMPACT
    assert card.summary == "ciclo 1"
    assert (card.evidence, card.source_url) == ("taxa em 12%", evidence.url)
    assert card.page_url == page_url(incident.id)
    assert types(decision) == [EventType.CYCLE_COMPLETED, EventType.NOTIFICATION_QUEUED]
    completed_event = decision.events[0]
    assert completed_event.author == AGENT_AUTHOR
    assert completed_event.data == {
        "number": 1,
        "trend": "estavel",
        "error_rate": 12.0,
        "summary": "ciclo 1",
        "changed": False,
        "posted": True,
        "evaluations": 0,
    }


def test_worse_after_stable_posts_the_update_card() -> None:
    history = (cycle(1, 6, posted=True),)

    decision = decide(p1(), cycle(2, 11, Trend.WORSE), history)

    [card] = decision.notifications
    assert card.cycle is not None
    assert card.cycle.trend is Trend.WORSE
    assert card.evidence == "12% de erro"


def test_model_saying_something_changed_posts_the_update_card() -> None:
    history = (cycle(1, 6, posted=True),)

    decision = decide(p1(), cycle(2, 11, changed=True), history)

    assert decision.cycle.posted
    [card] = decision.notifications
    assert card.cycle is not None


def test_three_stable_cycles_post_nothing_until_the_quiet_time_then_the_short_card() -> None:
    first = cycle(1, 6, posted=True)
    second, third = cycle(2, 11), cycle(3, 16)

    assert decide(p1(), second, (first,)).notifications == ()
    assert decide(p1(), third, (first, second)).notifications == ()
    short = decide(p1(), cycle(4, 21), (first, second, third))

    [card] = short.notifications
    assert card.kind is NotificationKind.FOLLOW_UP_UPDATE
    assert card.cycle is None
    assert card.summary == "sem mudança: estável, 12% de erro"
    assert card.impact == IMPACT
    assert short.cycle.posted
    assert short.events[0].data["posted"] is True


def test_trend_is_compared_with_the_last_posted_card_not_the_last_cycle() -> None:
    history = (cycle(1, 6, Trend.WORSE, posted=True), cycle(2, 11, Trend.STABLE))

    decision = decide(p1(), cycle(3, 16, Trend.WORSE), history)

    assert decision.notifications == ()


def test_partial_cycle_is_on_the_timeline_and_posts_nothing() -> None:
    job = cycle(2, 11, None, rate=None, status=CycleStatus.RUNNING)
    partial = partial_cycle(job, PartialReason.BUDGET, (), at(12))

    decision = decide(p1(), partial, (cycle(1, 6, posted=True),))

    assert decision.notifications == ()
    assert not decision.cycle.posted
    assert types(decision) == [EventType.CYCLE_PARTIAL]
    assert decision.events[0].data == {
        "number": 2,
        "reason": "orcamento_esgotado",
        "summary": "Ciclo parcial: orçamento de tokens esgotado.",
        "evaluations": 0,
    }


def test_last_allowed_cycle_stops_the_follow_up() -> None:
    policy = replace(POLICY, max_cycles=2)

    decision = decide(p1(), cycle(2, 11), (cycle(1, 6, posted=True),), policy=policy)

    stopped = [e for e in decision.events if e.type is EventType.FOLLOW_UP_STOPPED]
    assert [e.data for e in stopped] == [{"reason": LIMIT_REACHED, "cycles": 2}]


def test_partial_last_cycle_also_stops_the_follow_up() -> None:
    policy = replace(POLICY, max_cycles=1)
    job = cycle(1, 6, None, status=CycleStatus.RUNNING)

    decision = decide(
        p1(), partial_cycle(job, PartialReason.INTERRUPTED, (), at(12)), policy=policy
    )

    assert types(decision) == [EventType.CYCLE_PARTIAL, EventType.FOLLOW_UP_STOPPED]


# ---- decide_cycle: propostas ----


def normal(number: int, minutes: float, *, posted: bool = False) -> Cycle:
    return cycle(number, minutes, Trend.NORMAL, rate=0.0, posted=posted)


@pytest.mark.parametrize(
    ("state", "proposal", "text"),
    [
        (IncidentState.HANDLING, Proposal.MITIGATE, "Grolla sugere marcar como mitigado"),
        (IncidentState.MITIGATED, Proposal.RESOLVE, "Grolla sugere encerrar"),
    ],
)
def test_normal_for_fifteen_minutes_proposes_the_next_step(
    state: IncidentState, proposal: Proposal, text: str
) -> None:
    incident = p1(state)
    history = (normal(1, 6, posted=True), normal(2, 11), normal(3, 16))

    decision = decide(incident, normal(4, 21), history)

    proposals = [e for e in decision.events if e.type is EventType.AGENT_PROPOSAL]
    reason = "serviço normalizado há 15 min, 0% de erro"
    assert [e.data for e in proposals] == [
        {"proposal": proposal.value, "state": state.value, "reason": reason}
    ]
    assert proposals[0].author == AGENT_AUTHOR
    [card] = [n for n in decision.notifications if n.kind is NotificationKind.AGENT_PROPOSAL]
    assert (card.summary, card.evidence) == (text, reason)
    assert card.page_url == page_url(incident.id)
    queued = [e.data for e in decision.events if e.type is EventType.NOTIFICATION_QUEUED]
    assert {"kind": "agent_proposal"} in queued


def test_no_proposal_before_fifteen_minutes_of_normal() -> None:
    history = (normal(1, 6, posted=True), normal(2, 11))

    decision = decide(p1(), normal(3, 16), history)

    assert EventType.AGENT_PROPOSAL not in types(decision)


def test_worse_after_ten_minutes_of_normal_restarts_the_count() -> None:
    history = (normal(1, 6, posted=True), normal(2, 11), normal(3, 16))
    worse = cycle(4, 21, Trend.WORSE, posted=True)

    assert EventType.AGENT_PROPOSAL not in types(decide(p1(), worse, history))
    later = decide(p1(), normal(5, 26), (*history, worse))
    assert EventType.AGENT_PROPOSAL not in types(later)
    after = (*history, worse, normal(5, 26, posted=True), normal(6, 31), normal(7, 36))
    assert EventType.AGENT_PROPOSAL in types(decide(p1(), normal(8, 41), after))


def test_partial_cycles_do_not_interrupt_the_normal_count() -> None:
    gap = cycle(2, 11, None, rate=None, status=CycleStatus.PARTIAL)
    history = (normal(1, 6, posted=True), gap, normal(3, 16))

    decision = decide(p1(), normal(4, 21), history)

    assert EventType.AGENT_PROPOSAL in types(decision)


def test_same_proposal_is_not_repeated_in_the_same_state() -> None:
    history = (normal(1, 6, posted=True), normal(2, 11), normal(3, 16), normal(4, 21))
    made = (MadeProposal(Proposal.MITIGATE, IncidentState.HANDLING),)

    again = decide(p1(), normal(5, 26), history, made)
    mitigated = decide(p1(IncidentState.MITIGATED), normal(5, 26), history, made)

    assert EventType.AGENT_PROPOSAL not in types(again)
    assert [e.data["proposal"] for e in mitigated.events if e.type is EventType.AGENT_PROPOSAL] == [
        "encerrar"
    ]


def test_closed_incident_gets_no_proposal() -> None:
    history = (normal(1, 6, posted=True), normal(2, 11), normal(3, 16))

    decision = decide(p1(IncidentState.RESOLVED), normal(4, 21), history)

    assert EventType.AGENT_PROPOSAL not in types(decision)


# ---- impacto ----


def test_impact_of_pedidos_api_is_three_thousand() -> None:
    cycles = [cycle(1, 5, rate=50.0), cycle(2, 10, rate=10.0)]

    assert estimated_impact(cycles, T0, per_minute=1000) == pytest.approx(3000)


def test_cycles_without_error_rate_do_not_count_and_the_next_covers_their_time() -> None:
    cycles = [
        cycle(1, 5, rate=None),
        cycle(2, 10, None, rate=None, status=CycleStatus.PARTIAL),
        cycle(3, 10, rate=10.0),
        cycle(4, 15, status=CycleStatus.RUNNING),
    ]

    assert estimated_impact(cycles, T0, per_minute=1000) == pytest.approx(1000)
    assert estimated_impact([], T0, per_minute=1000) == 0


@pytest.mark.parametrize(
    ("rate", "text"),
    [(12.0, "12% de erro"), (0.5, "0,5% de erro"), (0.0, "0% de erro"), (None, "erro não medido")],
)
def test_error_rate_text(rate: float | None, text: str) -> None:
    assert error_rate_text(rate) == text


@pytest.mark.parametrize("state", [IncidentState.DETECTED, IncidentState.INFORMED])
def test_without_an_ic_there_is_no_proposal_to_mitigate(state: IncidentState) -> None:
    """Mitigar pressupõe alguém conduzindo o incidente: sem IC, não há o que propor."""
    history = (normal(1, 6, posted=True), normal(2, 11), normal(3, 16))

    decision = decide(p1(state), normal(4, 21), history)

    assert not [e for e in decision.events if e.type is EventType.AGENT_PROPOSAL]
