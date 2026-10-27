"""Triagem do incidente novo e junção de incidentes: decisões puras."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.domain.incidents import (
    SYSTEM_AUTHOR,
    EventType,
    IncidentState,
    NotificationKind,
    Severity,
)
from grolla.domain.triage import (
    TriageDraft,
    decide_merge,
    decide_triage,
    needs_model,
)
from grolla.domain.war_room import InvalidActionError
from grolla.testing.builders import T0, make_incident

NOW = T0 + timedelta(minutes=2)


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


def draft(
    same_as: UUID | None = None, severity: Severity | None = None, reason: str = "autorizador"
) -> TriageDraft:
    return TriageDraft(same_as, "os dois usam grolla-lab-db", severity, reason)


# ---- needs_model ----


def test_alone_and_classified_by_rule_skips_the_model() -> None:
    incident = make_incident(services=("captura-api",))
    same_service = make_incident(services=("captura-api",))

    assert not needs_model(incident, [], opened_by_default=False)
    assert not needs_model(incident, [same_service], opened_by_default=False)


def test_other_service_or_default_severity_needs_the_model() -> None:
    incident = make_incident(services=("captura-api",))
    other = make_incident(services=("pedidos-api",))

    assert needs_model(incident, [other], opened_by_default=False)
    assert needs_model(incident, [], opened_by_default=True)


# ---- decide_triage ----


def test_without_draft_only_completes_the_triage() -> None:
    incident = make_incident()

    decision = decide_triage(
        incident, others=[], draft=None, opened_by_default=False, now=NOW, page_url=page_url
    )

    assert decision.incident is None
    assert [(e.type, e.data) for e in decision.events] == [
        (EventType.TRIAGE_COMPLETED, {"same_as": None, "severity": None})
    ]
    assert decision.notifications == ()


def test_same_problem_suggests_the_merge_with_event_and_card() -> None:
    other = replace(make_incident(services=("pedidos-api",)), title="Erro alto em pedidos")
    incident = make_incident(services=("captura-api",))

    decision = decide_triage(
        incident,
        others=[other],
        draft=draft(same_as=other.id),
        opened_by_default=False,
        now=NOW,
        page_url=page_url,
    )

    assert decision.incident is None
    assert [(e.type, e.data) for e in decision.events] == [
        (
            EventType.MERGE_SUGGESTED,
            {"other": str(other.id), "reason": "os dois usam grolla-lab-db"},
        ),
        (EventType.TRIAGE_COMPLETED, {"same_as": str(other.id), "severity": None}),
        (EventType.NOTIFICATION_QUEUED, {"kind": "merge_suggested"}),
    ]
    assert all(e.incident_id == incident.id for e in decision.events)
    (card,) = decision.notifications
    assert card.kind is NotificationKind.MERGE_SUGGESTED
    assert card.incident_id == incident.id
    assert card.summary == "Parece o mesmo problema que Erro alto em pedidos"
    assert card.evidence == "os dois usam grolla-lab-db"
    assert card.page_url == page_url(incident.id)


def test_unknown_same_as_is_ignored() -> None:
    incident = make_incident()

    decision = decide_triage(
        incident,
        others=[make_incident()],
        draft=draft(same_as=uuid4()),
        opened_by_default=False,
        now=NOW,
        page_url=page_url,
    )

    assert [e.type for e in decision.events] == [EventType.TRIAGE_COMPLETED]


def test_higher_suggestion_raises_the_default_severity_and_the_state_follows() -> None:
    incident = make_incident(severity=Severity.P3, state=IncidentState.INFORMED)

    decision = decide_triage(
        incident,
        others=[],
        draft=draft(severity=Severity.P1, reason="o serviço é o autorizador"),
        opened_by_default=True,
        now=NOW,
        page_url=page_url,
    )

    assert decision.incident == replace(
        incident, severity=Severity.P1, state=IncidentState.DETECTED
    )
    assert [(e.type, e.data, e.author) for e in decision.events] == [
        (
            EventType.SEVERITY_CHANGED,
            {"from": "P3", "to": "P1", "reason": "sugerida pelo agente: o serviço é o autorizador"},
            SYSTEM_AUTHOR,
        ),
        (EventType.STATE_CHANGED, {"from": "informado", "to": "detectado"}, SYSTEM_AUTHOR),
        (EventType.TRIAGE_COMPLETED, {"same_as": None, "severity": "P1"}, SYSTEM_AUTHOR),
        (EventType.NOTIFICATION_QUEUED, {"kind": "severity_raised"}, SYSTEM_AUTHOR),
    ]
    (card,) = decision.notifications
    assert card.kind is NotificationKind.SEVERITY_RAISED
    assert card.severity is Severity.P1
    assert card.summary == "P3 → P1 sugerida pelo agente"
    assert card.evidence == "o serviço é o autorizador"


def test_raised_severity_keeps_a_handled_state() -> None:
    incident = make_incident(severity=Severity.P3, state=IncidentState.HANDLING)

    decision = decide_triage(
        incident,
        others=[],
        draft=draft(severity=Severity.P2),
        opened_by_default=True,
        now=NOW,
        page_url=page_url,
    )

    assert decision.incident == replace(incident, severity=Severity.P2)
    assert EventType.STATE_CHANGED not in [e.type for e in decision.events]


@pytest.mark.parametrize("suggested", [Severity.P3, Severity.P2])
def test_lower_or_equal_suggestion_never_changes_the_severity(suggested: Severity) -> None:
    incident = make_incident(severity=Severity.P2)

    decision = decide_triage(
        incident,
        others=[],
        draft=draft(severity=suggested),
        opened_by_default=True,
        now=NOW,
        page_url=page_url,
    )

    assert decision.incident is None
    assert [(e.type, e.data) for e in decision.events] == [
        (EventType.TRIAGE_COMPLETED, {"same_as": None, "severity": None})
    ]


def test_suggestion_is_ignored_when_a_rule_classified_the_alert() -> None:
    incident = make_incident(severity=Severity.P3)

    decision = decide_triage(
        incident,
        others=[],
        draft=draft(severity=Severity.P0),
        opened_by_default=False,
        now=NOW,
        page_url=page_url,
    )

    assert decision.incident is None
    assert decision.notifications == ()


def test_merge_suggestion_and_raised_severity_together() -> None:
    other = make_incident(services=("pedidos-api",))
    incident = make_incident(severity=Severity.P3, state=IncidentState.INFORMED)

    decision = decide_triage(
        incident,
        others=[other],
        draft=draft(same_as=other.id, severity=Severity.P2),
        opened_by_default=True,
        now=NOW,
        page_url=page_url,
    )

    assert [n.kind for n in decision.notifications] == [
        NotificationKind.SEVERITY_RAISED,
        NotificationKind.MERGE_SUGGESTED,
    ]
    assert all(n.severity is Severity.P2 for n in decision.notifications)


# ---- decide_merge ----


def test_merge_discards_the_source_and_moves_everything_to_the_target() -> None:
    target = replace(
        make_incident(
            services=("pedidos-api",),
            severity=Severity.P2,
            state=IncidentState.HANDLING,
            last_alert_at=T0,
            firing=frozenset({"a"}),
        ),
        title="Erro alto em pedidos",
    )
    source = replace(
        make_incident(
            services=("captura-api", "pedidos-api"),
            severity=Severity.P1,
            state=IncidentState.DETECTED,
            last_alert_at=T0 + timedelta(minutes=1),
            firing=frozenset({"b"}),
        ),
        title="Erro alto em captura",
    )

    decision = decide_merge(source, target, author=" Ana ", now=NOW, page_url=page_url)

    assert decision.source == replace(
        source, state=IncidentState.DISCARDED, resolved_at=NOW, firing=frozenset()
    )
    assert decision.target == replace(
        target,
        services=("pedidos-api", "captura-api"),
        firing=frozenset({"a", "b"}),
        last_alert_at=T0 + timedelta(minutes=1),
        severity=Severity.P1,
    )
    assert [(e.incident_id, e.type, e.data, e.author) for e in decision.events] == [
        (
            source.id,
            EventType.INCIDENT_DISCARDED,
            {"reason": "juntado em Erro alto em pedidos", "from": "detectado", "to": "descartado"},
            "Ana",
        ),
        (source.id, EventType.MERGED_INTO, {"into": str(target.id)}, "Ana"),
        (target.id, EventType.SEVERITY_CHANGED, {"from": "P2", "to": "P1"}, "Ana"),
        (target.id, EventType.INCIDENT_MERGED, {"merged": str(source.id)}, "Ana"),
        (target.id, EventType.NOTIFICATION_QUEUED, {"kind": "incident_merged"}, SYSTEM_AUTHOR),
    ]
    (card,) = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_MERGED
    assert card.incident_id == target.id
    assert card.severity is Severity.P1
    assert card.services == ("pedidos-api", "captura-api")
    assert card.summary == "Ana juntou Erro alto em captura neste incidente"
    assert card.page_url == page_url(target.id)


def test_merge_keeps_the_worse_target_severity_and_its_waiting_state_follows() -> None:
    target = make_incident(severity=Severity.P3, state=IncidentState.INFORMED)
    source = make_incident(severity=Severity.P0, state=IncidentState.DETECTED)

    decision = decide_merge(source, target, author="Ana", now=NOW, page_url=page_url)

    assert decision.target.severity is Severity.P0
    assert decision.target.state is IncidentState.DETECTED
    worse = make_incident(severity=Severity.P0)
    kept = decide_merge(make_incident(), worse, author="Ana", now=NOW, page_url=page_url)
    assert kept.target.severity is Severity.P0
    assert EventType.SEVERITY_CHANGED not in [e.type for e in kept.events]


def test_merge_refuses_the_same_incident_no_author_and_closed_incidents() -> None:
    incident = make_incident()
    closed = make_incident(state=IncidentState.RESOLVED)

    with pytest.raises(InvalidActionError, match="nele mesmo"):
        decide_merge(incident, incident, author="Ana", now=NOW, page_url=page_url)
    with pytest.raises(InvalidActionError, match="seu nome"):
        decide_merge(incident, make_incident(), author=" ", now=NOW, page_url=page_url)
    with pytest.raises(InvalidActionError, match="abertos"):
        decide_merge(incident, closed, author="Ana", now=NOW, page_url=page_url)
    with pytest.raises(InvalidActionError, match="abertos"):
        decide_merge(closed, incident, author="Ana", now=NOW, page_url=page_url)
