from datetime import timedelta
from uuid import UUID

from grolla.application.incidents import IntakePolicy, deliver_notifications, receive_alerts
from grolla.domain.analysis import AnalysisStatus
from grolla.domain.incidents import (
    AlertStatus,
    EventType,
    NotificationKind,
    Severity,
    SeverityPolicy,
    SeverityRule,
)
from grolla.testing.builders import (
    T0,
    make_alert,
    make_incident,
    make_notification,
    make_verification,
)
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import (
    FakeChatNotifier,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)

TEAM = {"team": "credenciadora"}
POLICY = IntakePolicy(
    label_filter=TEAM,
    severity=SeverityPolicy(
        rules=(SeverityRule({"severity": "critical"}, Severity.P1),), default=Severity.P3
    ),
    window=timedelta(minutes=15),
    page_url=lambda incident_id: f"https://grolla.interno/incidents/{incident_id}",
)


def team_alert(service: str = "authorizer-api", **labels: str) -> dict[str, str]:
    return {**TEAM, "service_name": service, **labels}


async def test_new_alert_opens_an_incident_with_its_events_alert_and_card() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(service="authorizer-api", labels=team_alert())

    await receive_alerts([alert], repository, POLICY, FakeClock(T0))

    [incident] = await repository.list_incidents(10)
    assert incident.severity is Severity.P3
    assert await repository.alerts_of(incident.id) == [alert]
    assert [e.type for e in await repository.events_of(incident.id)] == [
        EventType.ALERT_RECEIVED,
        EventType.STATE_CHANGED,
        EventType.ANALYSIS_REQUESTED,
        EventType.NOTIFICATION_QUEUED,
    ]
    [entry] = repository.outbox
    assert entry.notification.kind is NotificationKind.INCIDENT_OPENED
    assert entry.next_attempt_at == T0
    analysis = await repository.analysis_of(incident.id)
    assert analysis is not None
    assert analysis.status is AnalysisStatus.PENDING
    assert analysis.requested_at == T0


async def test_grouped_alert_does_not_request_another_analysis() -> None:
    repository = InMemoryIncidentRepository()
    first = make_alert(service="authorizer-api", labels=team_alert())
    second = make_alert(service="authorizer-api", labels=team_alert())
    clock = FakeClock(T0)

    await receive_alerts([first], repository, POLICY, clock)
    clock.advance(timedelta(minutes=2))
    await receive_alerts([second], repository, POLICY, clock)

    [incident] = await repository.list_incidents(10)
    events = [e.type for e in await repository.events_of(incident.id)]
    assert events.count(EventType.ANALYSIS_REQUESTED) == 1
    assert list(repository.analyses) == [incident.id]
    assert repository.analyses[incident.id].requested_at == T0
    # O alerta agrupado antecipa o próximo ciclo de acompanhamento; o que abriu, não.
    assert repository.cycle_requests == {incident.id: T0 + timedelta(minutes=2)}


async def test_resolved_alert_does_not_bring_the_cycle_forward() -> None:
    repository = InMemoryIncidentRepository()
    firing = make_alert(service="authorizer-api", labels=team_alert())
    resolved = make_alert(
        fingerprint=firing.fingerprint, status=AlertStatus.RESOLVED, labels=team_alert()
    )

    await receive_alerts([firing, resolved], repository, POLICY, FakeClock(T0))

    assert repository.cycle_requests == {}


async def test_resent_alert_changes_nothing() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(labels=team_alert())

    await receive_alerts([alert, alert, alert], repository, POLICY, FakeClock(T0))

    [incident] = await repository.list_incidents(10)
    assert len(await repository.events_of(incident.id)) == 4
    assert len(repository.outbox) == 1


async def test_alert_of_another_team_is_ignored() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(labels={"team": "liquidacao", "service_name": "x"})

    await receive_alerts([alert], repository, POLICY, FakeClock(T0))

    assert await repository.list_incidents(10) == []
    assert repository.seen == set()


async def test_same_service_groups_and_graver_alert_escalates() -> None:
    repository = InMemoryIncidentRepository()
    clock = FakeClock(T0)
    first = make_alert(service="authorizer-api", labels=team_alert())
    second = make_alert(service="authorizer-api", labels=team_alert(severity="critical"))

    await receive_alerts([first], repository, POLICY, clock)
    clock.advance(timedelta(minutes=5))
    await receive_alerts([second], repository, POLICY, clock)

    [incident] = await repository.list_incidents(10)
    assert incident.severity is Severity.P1
    assert await repository.alerts_of(incident.id) == [first, second]
    assert [entry.notification.kind for entry in repository.outbox] == [
        NotificationKind.INCIDENT_OPENED,
        NotificationKind.SEVERITY_RAISED,
    ]


async def test_resolved_alerts_normalize_the_incident() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(labels=team_alert())
    resolved = make_alert(
        fingerprint=alert.fingerprint, status=AlertStatus.RESOLVED, labels=team_alert()
    )

    await receive_alerts([alert, resolved], repository, POLICY, FakeClock(T0))

    [incident] = await repository.list_incidents(10)
    assert incident.firing == frozenset()
    assert EventType.INCIDENT_NORMALIZED in [
        e.type for e in await repository.events_of(incident.id)
    ]
    assert repository.outbox[-1].notification.kind is NotificationKind.INCIDENT_NORMALIZED


async def test_normalization_card_carries_the_verification() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(labels=team_alert())
    await receive_alerts([alert], repository, POLICY, FakeClock(T0))
    [incident] = await repository.list_incidents(10)
    verification = make_verification(incident.id)
    repository.verifications[incident.id] = verification
    resolved = make_alert(
        fingerprint=alert.fingerprint, status=AlertStatus.RESOLVED, labels=team_alert()
    )

    await receive_alerts([resolved], repository, POLICY, FakeClock(T0 + timedelta(minutes=32)))

    card = repository.outbox[-1].notification
    assert card.kind is NotificationKind.INCIDENT_NORMALIZED
    assert card.verification == verification
    assert card.summary == "Normalizado: durou 32 min"


async def test_normalization_without_verification_has_none() -> None:
    repository = InMemoryIncidentRepository()
    alert = make_alert(labels=team_alert())
    resolved = make_alert(
        fingerprint=alert.fingerprint, status=AlertStatus.RESOLVED, labels=team_alert()
    )

    await receive_alerts([alert, resolved], repository, POLICY, FakeClock(T0))

    assert repository.outbox[-1].notification.verification is None


async def test_resolved_alert_without_incident_is_ignored() -> None:
    repository = InMemoryIncidentRepository()
    resolved = make_alert(status=AlertStatus.RESOLVED, labels=team_alert())

    await receive_alerts([resolved], repository, POLICY, FakeClock(T0))

    assert await repository.list_incidents(10) == []


async def seeded_outbox(
    *incident_ids: UUID,
) -> tuple[InMemoryIncidentRepository, InMemoryNotificationOutbox]:
    repository = InMemoryIncidentRepository()
    async with repository.transaction() as uow:
        for incident_id in incident_ids:
            await uow.save(make_incident(id=incident_id))
            await uow.enqueue(make_notification(incident_id), T0)
    return repository, InMemoryNotificationOutbox(repository)


async def test_delivers_due_cards_and_records_the_delivery() -> None:
    incident = make_incident()
    repository, outbox = await seeded_outbox(incident.id)
    notifier = FakeChatNotifier()

    assert await deliver_notifications(outbox, notifier, FakeClock(T0)) == 1

    assert len(notifier.posted) == 1
    [event] = await repository.events_of(incident.id)
    assert event.type is EventType.NOTIFICATION_DELIVERED
    assert await deliver_notifications(outbox, notifier, FakeClock(T0)) == 0


async def test_failing_channel_is_retried_with_growing_wait_until_delivered() -> None:
    incident = make_incident()
    repository, outbox = await seeded_outbox(incident.id)
    notifier = FakeChatNotifier(failures=2)
    clock = FakeClock(T0)

    assert await deliver_notifications(outbox, notifier, clock) == 0
    assert repository.outbox[0].next_attempt_at == T0 + timedelta(seconds=5)
    clock.advance(timedelta(seconds=5))
    assert await deliver_notifications(outbox, notifier, clock) == 0
    assert repository.outbox[0].next_attempt_at == T0 + timedelta(seconds=15)
    clock.advance(timedelta(seconds=9))
    assert await deliver_notifications(outbox, notifier, clock) == 0  # ainda esperando
    clock.advance(timedelta(seconds=1))
    assert await deliver_notifications(outbox, notifier, clock) == 1

    assert len(notifier.posted) == 1
    assert [e.type for e in await repository.events_of(incident.id)] == [
        EventType.NOTIFICATION_DELIVERED
    ]


async def test_gives_up_after_the_attempt_limit() -> None:
    incident = make_incident()
    repository, outbox = await seeded_outbox(incident.id)
    notifier = FakeChatNotifier(failures=10)
    clock = FakeClock(T0)

    for _ in range(3):
        await deliver_notifications(outbox, notifier, clock, max_attempts=3)
        clock.advance(timedelta(minutes=10))

    assert len(notifier.attempts) == 3
    [event] = await repository.events_of(incident.id)
    assert event.type is EventType.NOTIFICATION_FAILED
    assert await outbox.due(clock.now(), 10) == []


async def test_failure_holds_the_later_cards_of_the_same_incident_only() -> None:
    first, second = make_incident(), make_incident()
    repository, outbox = await seeded_outbox(first.id)
    async with repository.transaction() as uow:
        await uow.enqueue(make_notification(first.id, NotificationKind.SEVERITY_RAISED), T0)
        await uow.save(second)
        await uow.enqueue(make_notification(second.id), T0)
    notifier = FakeChatNotifier(failures=1)

    assert await deliver_notifications(outbox, notifier, FakeClock(T0)) == 1

    assert [n.incident_id for n in notifier.attempts] == [first.id, second.id]
