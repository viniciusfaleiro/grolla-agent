"""Ações humanas no war room: transições, horários, eventos com autor e cards."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from grolla.domain.incidents import (
    Decision,
    EventType,
    Incident,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.domain.war_room import (
    AssumeCommander,
    ChangeSeverity,
    DiscardHypothesis,
    DiscardIncident,
    HumanAction,
    InvalidActionError,
    MarkMitigated,
    RecordAction,
    Reopen,
    Resolve,
    action_text,
    decide_action,
)
from grolla.testing.builders import T0, make_incident

NOW = T0 + timedelta(minutes=4)
OPEN = [
    IncidentState.DETECTED,
    IncidentState.INFORMED,
    IncidentState.HANDLING,
    IncidentState.MITIGATED,
]
CLOSED = [IncidentState.RESOLVED, IncidentState.DISCARDED]


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


def act(incident: Incident, action: HumanAction, author: str = "Ana") -> Decision:
    return decide_action(incident, action, author, NOW, page_url)


def updated(decision: Decision) -> Incident:
    assert decision.incident is not None
    return decision.incident


def types(decision: Decision) -> list[EventType]:
    return [event.type for event in decision.events]


@pytest.mark.parametrize("state", [IncidentState.DETECTED, IncidentState.INFORMED])
def test_assuming_a_waiting_incident_starts_the_handling(state: IncidentState) -> None:
    decision = act(make_incident(state=state), AssumeCommander())

    incident = updated(decision)
    assert incident.state is IncidentState.HANDLING
    assert incident.commander == "Ana"
    assert incident.handling_at == NOW
    assert types(decision) == [EventType.COMMANDER_ASSIGNED, EventType.NOTIFICATION_QUEUED]
    event = decision.events[0]
    assert event.author == "Ana"
    assert event.at == NOW
    assert event.data == {
        "commander": "Ana",
        "previous": None,
        "from": state.value,
        "to": IncidentState.HANDLING.value,
    }
    (card,) = decision.notifications
    assert card.kind is NotificationKind.COMMANDER_ASSIGNED
    assert card.summary == "Ana assumiu como IC"
    assert card.page_url == page_url(incident.id)


@pytest.mark.parametrize("state", [IncidentState.HANDLING, IncidentState.MITIGATED])
def test_assuming_a_handled_incident_only_changes_the_commander(state: IncidentState) -> None:
    started = T0 + timedelta(minutes=1)
    incident = replace(make_incident(state=state), commander="Ana", handling_at=started)

    decision = act(incident, AssumeCommander(), author="Bruno")

    assert updated(decision).state is state
    assert updated(decision).commander == "Bruno"
    assert updated(decision).handling_at == started
    assert decision.events[0].data["previous"] == "Ana"
    assert decision.notifications[0].summary == "Bruno assumiu como IC"


def test_the_commander_cannot_assume_again() -> None:
    incident = replace(make_incident(state=IncidentState.HANDLING), commander="Ana")

    with pytest.raises(InvalidActionError, match="Ana já é o IC"):
        act(incident, AssumeCommander(), author=" Ana ")


@pytest.mark.parametrize(
    ("before", "after"), [(Severity.P1, Severity.P3), (Severity.P3, Severity.P0)]
)
def test_a_human_moves_the_severity_up_or_down(before: Severity, after: Severity) -> None:
    incident = make_incident(severity=before, state=IncidentState.HANDLING)

    decision = act(incident, ChangeSeverity(after))

    assert updated(decision).severity is after
    assert updated(decision).state is IncidentState.HANDLING
    assert types(decision) == [EventType.SEVERITY_CHANGED, EventType.NOTIFICATION_QUEUED]
    assert decision.events[0].data == {"from": before.value, "to": after.value}
    assert decision.events[0].author == "Ana"
    (card,) = decision.notifications
    assert card.kind is NotificationKind.SEVERITY_CHANGED_BY_HUMAN
    assert card.summary == f"{before.value} → {after.value} por Ana"
    assert card.severity is after


def test_same_severity_is_refused() -> None:
    with pytest.raises(InvalidActionError, match="já é P3"):
        act(make_incident(severity=Severity.P3), ChangeSeverity(Severity.P3))


def test_recording_an_action_only_goes_to_the_timeline() -> None:
    incident = make_incident(state=IncidentState.HANDLING)

    decision = act(incident, RecordAction("  fiz rollback do build 20261025.3 "))

    assert updated(decision) == incident
    assert types(decision) == [EventType.ACTION_RECORDED]
    assert decision.events[0].data == {"text": "fiz rollback do build 20261025.3"}
    assert decision.events[0].author == "Ana"
    assert decision.notifications == ()


def test_applying_a_command_records_the_step_the_command_and_where() -> None:
    applied = RecordAction(
        " Restaurar as réplicas ", " kubectl scale deploy/a --replicas=2 ", " cluster loja-prod "
    )

    decision = act(make_incident(state=IncidentState.HANDLING), applied)

    assert types(decision) == [EventType.ACTION_RECORDED]
    assert decision.events[0].data == {
        "text": "Restaurar as réplicas",
        "command": "kubectl scale deploy/a --replicas=2",
        "run_in": "cluster loja-prod",
    }


def test_blank_command_is_a_plain_recorded_action() -> None:
    decision = act(make_incident(), RecordAction("religuei o banco", "  ", "cluster x"))

    assert decision.events[0].data == {"text": "religuei o banco"}


def test_action_text_says_what_was_applied_and_where() -> None:
    data = {"text": "Restaurar", "command": "kubectl scale deploy/a", "run_in": "cluster x"}

    assert action_text(data) == "Aplicou: Restaurar — kubectl scale deploy/a (cluster x)"
    assert action_text(data | {"run_in": ""}) == "Aplicou: Restaurar — kubectl scale deploy/a"
    assert action_text({"text": "religuei o banco"}) == "religuei o banco"


def test_discarding_a_hypothesis_records_title_and_reason() -> None:
    decision = act(make_incident(), DiscardHypothesis("Hipótese 2", "banco saudável"))

    assert types(decision) == [EventType.HYPOTHESIS_DISCARDED]
    assert decision.events[0].data == {"title": "Hipótese 2", "reason": "banco saudável"}
    assert decision.notifications == ()


def test_marking_mitigated_records_the_time_and_posts_a_card() -> None:
    decision = act(make_incident(state=IncidentState.HANDLING), MarkMitigated())

    assert updated(decision).state is IncidentState.MITIGATED
    assert updated(decision).mitigated_at == NOW
    assert types(decision) == [EventType.INCIDENT_MITIGATED, EventType.NOTIFICATION_QUEUED]
    assert decision.events[0].data == {"from": "em_tratamento", "to": "mitigado"}
    (card,) = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_MITIGATED
    assert card.summary == "Mitigado por Ana"


@pytest.mark.parametrize(
    "state", [IncidentState.DETECTED, IncidentState.INFORMED, IncidentState.MITIGATED]
)
def test_only_a_handled_incident_is_mitigated(state: IncidentState) -> None:
    with pytest.raises(InvalidActionError, match="em tratamento"):
        act(make_incident(state=state), MarkMitigated())


def test_reopening_a_mitigated_incident_goes_back_to_handling() -> None:
    mitigated_at = T0 + timedelta(minutes=2)
    incident = replace(make_incident(state=IncidentState.MITIGATED), mitigated_at=mitigated_at)

    decision = act(incident, Reopen())

    assert updated(decision).state is IncidentState.HANDLING
    assert updated(decision).mitigated_at == mitigated_at
    assert types(decision) == [EventType.INCIDENT_REOPENED]
    assert decision.notifications == ()


def test_only_a_mitigated_incident_is_reopened() -> None:
    with pytest.raises(InvalidActionError, match="mitigado pode ser reaberto"):
        act(make_incident(state=IncidentState.HANDLING), Reopen())


@pytest.mark.parametrize("state", OPEN)
def test_resolving_closes_with_the_duration_and_the_reason(state: IncidentState) -> None:
    incident = make_incident(state=state, detected_at=T0 - timedelta(minutes=31))

    decision = act(incident, Resolve(" rollback do build .3 "))

    assert updated(decision).state is IncidentState.RESOLVED
    assert updated(decision).resolved_at == NOW
    assert types(decision) == [EventType.INCIDENT_RESOLVED, EventType.NOTIFICATION_QUEUED]
    assert decision.events[0].data == {
        "reason": "rollback do build .3",
        "from": state.value,
        "to": "resolvido",
    }
    (card,) = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_RESOLVED
    assert card.summary == "Encerrado por Ana · durou 35 min"
    assert card.evidence == "Motivo: rollback do build .3"


@pytest.mark.parametrize("state", OPEN)
def test_discarding_closes_as_false_positive(state: IncidentState) -> None:
    decision = act(make_incident(state=state), DiscardIncident("alerta de teste"))

    assert updated(decision).state is IncidentState.DISCARDED
    assert updated(decision).resolved_at == NOW
    assert types(decision) == [EventType.INCIDENT_DISCARDED, EventType.NOTIFICATION_QUEUED]
    (card,) = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_DISCARDED
    assert card.summary == "Descartado por Ana"
    assert card.evidence == "Motivo: alerta de teste"


@pytest.mark.parametrize(
    "action",
    [
        Resolve("  "),
        DiscardIncident(""),
        RecordAction(" "),
        DiscardHypothesis("", "motivo"),
        DiscardHypothesis("Hipótese 1", ""),
    ],
)
def test_required_text_is_refused_when_empty(action: HumanAction) -> None:
    with pytest.raises(InvalidActionError, match=r"obrigatório|descreva|informe"):
        act(make_incident(state=IncidentState.HANDLING), action)


def test_action_without_author_is_refused() -> None:
    with pytest.raises(InvalidActionError, match="seu nome"):
        act(make_incident(), AssumeCommander(), author="  ")


@pytest.mark.parametrize("state", CLOSED)
@pytest.mark.parametrize(
    "action",
    [
        AssumeCommander(),
        ChangeSeverity(Severity.P0),
        RecordAction("texto"),
        DiscardHypothesis("Hipótese 1", "motivo"),
        MarkMitigated(),
        Reopen(),
        Resolve("motivo"),
        DiscardIncident("motivo"),
    ],
)
def test_closed_incident_refuses_every_action(state: IncidentState, action: HumanAction) -> None:
    with pytest.raises(InvalidActionError, match="o incidente já está encerrado"):
        act(make_incident(state=state), action)


def test_tta_ttm_and_ttr_from_the_detection() -> None:
    incident = make_incident(state=IncidentState.DETECTED, detected_at=T0)

    assumed = updated(
        decide_action(incident, AssumeCommander(), "Ana", T0 + timedelta(minutes=4), page_url)
    )
    mitigated = updated(
        decide_action(assumed, MarkMitigated(), "Ana", T0 + timedelta(minutes=20), page_url)
    )
    closed = updated(
        decide_action(mitigated, Resolve("ok"), "Ana", T0 + timedelta(minutes=35), page_url)
    )

    assert closed.handling_at is not None
    assert closed.mitigated_at is not None
    assert closed.resolved_at is not None
    assert closed.handling_at - closed.detected_at == timedelta(minutes=4)
    assert closed.mitigated_at - closed.detected_at == timedelta(minutes=20)
    assert closed.resolved_at - closed.detected_at == timedelta(minutes=35)
