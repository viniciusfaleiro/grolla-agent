from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.domain.incidents import (
    AlertStatus,
    Decision,
    EventType,
    Incident,
    IncidentState,
    NotificationKind,
    Severity,
    SeverityPolicy,
    SeverityRule,
    decide_firing,
    decide_resolved,
    worst,
)
from grolla.testing.builders import T0, make_alert, make_incident

WINDOW = timedelta(minutes=15)
POLICY = SeverityPolicy(
    rules=(
        SeverityRule({"alertname": "Queda de aprovação"}, Severity.P0),
        SeverityRule({"severity": "critical"}, Severity.P1),
        SeverityRule({"severity": "warning"}, Severity.P2),
    ),
    default=Severity.P3,
)
NEW_ID = UUID("00000000-0000-0000-0000-000000000001")


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


def firing(
    labels: dict[str, str] | None = None,
    candidates: tuple[Incident, ...] = (),
    service: str = "authorizer-api",
    minutes: int = 0,
) -> Decision:
    alert = make_alert(service=service, labels={"service_name": service, **(labels or {})})
    return decide_firing(
        alert,
        candidates=candidates,
        policy=POLICY,
        window=WINDOW,
        now=T0 + timedelta(minutes=minutes),
        page_url=page_url,
        new_id=lambda: NEW_ID,
    )


def types(decision: Decision) -> list[EventType]:
    return [event.type for event in decision.events]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Severity.P0, Severity.P3, Severity.P0),
        (Severity.P3, Severity.P1, Severity.P1),
        (Severity.P2, Severity.P2, Severity.P2),
    ],
)
def test_worst_is_the_most_severe(a: Severity, b: Severity, expected: Severity) -> None:
    assert worst(a, b) is expected


def test_first_matching_rule_wins() -> None:
    alert = make_alert(labels={"alertname": "Queda de aprovação", "severity": "critical"})
    assert POLICY.classify(alert) == (Severity.P0, 0)


def test_default_severity_without_matching_rule() -> None:
    assert POLICY.classify(make_alert(labels={"severity": "info"})) == (Severity.P3, None)


def test_p3_alert_opens_an_informed_incident_with_a_card() -> None:
    decision = firing()

    incident = decision.incident
    assert incident is not None
    assert incident.id == NEW_ID
    assert incident.state is IncidentState.INFORMED
    assert incident.severity is Severity.P3
    assert incident.services == ("authorizer-api",)
    assert incident.detected_at == T0
    assert len(incident.firing) == 1
    assert types(decision) == [
        EventType.ALERT_RECEIVED,
        EventType.STATE_CHANGED,
        EventType.ANALYSIS_REQUESTED,
        EventType.NOTIFICATION_QUEUED,
    ]
    assert decision.request_analysis
    assert decision.events[0].data["severity_reason"] == "severidade padrão"
    assert decision.events[1].data == {"from": "detectado", "to": "informado"}
    [card] = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_OPENED
    assert card.page_url == page_url(NEW_ID)
    assert card.evidence == make_alert().description


def test_p1_alert_opens_an_incident_that_stays_detected() -> None:
    decision = firing({"severity": "critical"})

    assert decision.incident is not None
    assert decision.incident.state is IncidentState.DETECTED
    assert types(decision) == [
        EventType.ALERT_RECEIVED,
        EventType.ANALYSIS_REQUESTED,
        EventType.NOTIFICATION_QUEUED,
    ]
    assert decision.request_analysis
    assert decision.events[0].data["severity_rule"] == 1
    assert decision.events[0].data["severity_reason"] == "regra 2"


def test_alert_of_the_same_service_in_the_window_is_grouped_without_card() -> None:
    current = make_incident(
        services=("authorizer-api",), firing=frozenset({"outro"}), severity=Severity.P2
    )

    decision = firing({"severity": "warning"}, candidates=(current,), minutes=5)

    incident = decision.incident
    assert incident is not None
    assert incident.id == current.id
    assert incident.last_alert_at == T0 + timedelta(minutes=5)
    assert "outro" in incident.firing
    assert len(incident.firing) == 2
    assert types(decision) == [EventType.ALERT_GROUPED]
    assert decision.events[0].data["reason"] == "mesmo serviço"
    assert decision.notifications == ()
    assert not decision.request_analysis


def test_most_recent_candidate_wins() -> None:
    older = make_incident(services=("authorizer-api",), detected_at=T0)
    newer = make_incident(services=("authorizer-api",), detected_at=T0 + timedelta(minutes=2))

    decision = firing(candidates=(older, newer), minutes=5)

    assert decision.incident is not None
    assert decision.incident.id == newer.id


def test_quiet_incident_outside_the_window_is_not_reused() -> None:
    old = make_incident(services=("authorizer-api",), detected_at=T0)

    decision = firing(candidates=(old,), minutes=20)

    assert decision.incident is not None
    assert decision.incident.id == NEW_ID


def test_firing_incident_outside_the_window_is_reused() -> None:
    old = make_incident(services=("authorizer-api",), firing=frozenset({"fp"}))

    decision = firing(candidates=(old,), minutes=60)

    assert decision.incident is not None
    assert decision.incident.id == old.id


def test_candidate_of_another_service_is_not_reused() -> None:
    other = make_incident(services=("capture-api",), firing=frozenset({"fp"}))

    decision = firing(candidates=(other,))

    assert decision.incident is not None
    assert decision.incident.id == NEW_ID


def test_informed_incident_escalated_to_p1_goes_back_to_detected_with_a_card() -> None:
    current = make_incident(services=("authorizer-api",), severity=Severity.P3)

    decision = firing({"severity": "critical"}, candidates=(current,), minutes=1)

    incident = decision.incident
    assert incident is not None
    assert incident.severity is Severity.P1
    assert incident.state is IncidentState.DETECTED
    assert types(decision) == [
        EventType.ALERT_GROUPED,
        EventType.SEVERITY_CHANGED,
        EventType.STATE_CHANGED,
        EventType.NOTIFICATION_QUEUED,
    ]
    assert decision.events[1].data == {"from": "P3", "to": "P1"}
    assert decision.events[2].data == {"from": "informado", "to": "detectado"}
    [card] = decision.notifications
    assert card.kind is NotificationKind.SEVERITY_RAISED
    assert card.severity is Severity.P1


def test_milder_alert_does_not_lower_the_severity() -> None:
    current = make_incident(
        services=("authorizer-api",), severity=Severity.P1, state=IncidentState.DETECTED
    )

    decision = firing(candidates=(current,), minutes=1)

    assert decision.incident is not None
    assert decision.incident.severity is Severity.P1
    assert decision.incident.state is IncidentState.DETECTED
    assert types(decision) == [EventType.ALERT_GROUPED]


def test_resolving_the_last_firing_alert_normalizes_the_incident() -> None:
    alert = make_alert(status=AlertStatus.RESOLVED)
    incident = make_incident(firing=frozenset({alert.fingerprint}))
    now = T0 + timedelta(minutes=10)

    decision = decide_resolved(alert, incident, now, page_url)

    assert decision.incident is not None
    assert decision.incident.firing == frozenset()
    assert decision.incident.last_alert_at == now
    assert types(decision) == [
        EventType.ALERT_RESOLVED,
        EventType.INCIDENT_NORMALIZED,
        EventType.NOTIFICATION_QUEUED,
    ]
    [card] = decision.notifications
    assert card.kind is NotificationKind.INCIDENT_NORMALIZED


def test_resolving_one_of_two_alerts_only_records_the_resolution() -> None:
    alert = make_alert(status=AlertStatus.RESOLVED)
    incident = make_incident(firing=frozenset({alert.fingerprint, uuid4().hex}))

    decision = decide_resolved(alert, incident, T0, page_url)

    assert decision.incident is not None
    assert len(decision.incident.firing) == 1
    assert types(decision) == [EventType.ALERT_RESOLVED]
    assert decision.notifications == ()
