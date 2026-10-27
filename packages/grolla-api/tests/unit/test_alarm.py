from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, Alarm, AlarmKind, alarm_normalized, alarm_of

NOW = T0 + timedelta(minutes=20)
NORMALIZED_AT = T0 + timedelta(minutes=15)


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    app = create_app(replace(services, repository=repository, clock=FakeClock(NOW)))
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def event(incident: Incident, kind: EventType, minutes: int) -> IncidentEvent:
    return IncidentEvent(incident.id, T0 + timedelta(minutes=minutes), kind, {}, author="sistema")


def normalized(
    state: IncidentState = IncidentState.HANDLING,
) -> tuple[Incident, list[IncidentEvent]]:
    incident = make_incident(state=state, last_alert_at=NORMALIZED_AT)
    events = [
        event(incident, EventType.ALERT_RECEIVED, 0),
        event(incident, EventType.ALERT_RESOLVED, 15),
        event(incident, EventType.INCIDENT_NORMALIZED, 15),
    ]
    return incident, events


def store(
    repository: InMemoryIncidentRepository, incident: Incident, events: list[IncidentEvent]
) -> None:
    repository.incidents[incident.id] = incident
    repository.events = events


def test_alarm_is_normalized_when_nothing_fires_after_an_alert() -> None:
    incident, events = normalized()

    assert alarm_of(incident, events) == Alarm(AlarmKind.NORMALIZED, NORMALIZED_AT)


def test_alarm_returned_when_an_alert_fires_after_normalizing() -> None:
    incident, events = normalized()
    firing = replace(incident, firing=frozenset({"fp"}))
    events += [
        event(incident, EventType.ALERT_GROUPED, 18),
        event(incident, EventType.ALERT_RECEIVED, 19),
    ]

    assert alarm_of(firing, events) == Alarm(AlarmKind.RETURNED, T0 + timedelta(minutes=18))


def test_no_alarm_while_firing_without_ever_normalizing() -> None:
    incident = make_incident(firing=frozenset({"fp"}))

    assert alarm_of(incident, [event(incident, EventType.ALERT_RECEIVED, 0)]) is None


def test_no_alarm_without_alerts() -> None:
    incident = make_incident()

    assert alarm_of(incident, [event(incident, EventType.INCIDENT_NORMALIZED, 1)]) is None


@pytest.mark.parametrize("state", [IncidentState.RESOLVED, IncidentState.DISCARDED])
def test_no_alarm_on_a_closed_incident(state: IncidentState) -> None:
    incident, events = normalized(state)

    assert alarm_of(incident, events) is None


def test_list_badge_needs_an_open_incident_that_had_an_alert_and_none_firing() -> None:
    incident, _ = normalized()

    assert alarm_normalized(incident)
    assert not alarm_normalized(replace(incident, firing=frozenset({"fp"})))
    assert not alarm_normalized(replace(incident, state=IncidentState.RESOLVED))
    assert not alarm_normalized(make_incident())  # aberto à mão, sem alerta


def test_page_shows_the_normalized_banner_with_mark_mitigated(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized()
    store(repository, incident, events)

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'data-alarm="normalizado"' in page
    assert "Alertas normalizados</strong> às" in page
    assert "há 5 min" in page
    assert f'hx-post="/incidents/{incident.id}/actions/mitigate" hx-target="#war-room"' in page


def test_mitigated_and_normalized_banner_opens_the_close_popover(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized(IncidentState.MITIGATED)
    store(repository, incident, events)

    banner = ana.get(f"/incidents/{incident.id}").text.split('id="alarm"')[1].split("</div>")[0]

    assert 'popovertarget="encerrar">Encerrar</button>' in banner


def test_banner_has_no_button_without_a_name(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized()
    store(repository, incident, events)

    banner = client.get(f"/incidents/{incident.id}").text.split('id="alarm"')[1].split("</div>")[0]

    assert "Alertas normalizados" in banner
    assert "<button" not in banner


def test_returned_banner(ana: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident, events = normalized()
    store(
        repository,
        replace(incident, firing=frozenset({"fp"})),
        [*events, event(incident, EventType.ALERT_RECEIVED, 18)],
    )

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'data-alarm="voltou"' in page
    assert "Voltou a disparar</strong> às" in page


def test_closed_incident_has_the_banner_hidden(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized(IncidentState.RESOLVED)
    store(repository, incident, events)

    page = ana.get(f"/incidents/{incident.id}").text

    assert 'data-alarm="" data-title' in page
    assert "Alertas normalizados" not in page


def test_timeline_polling_brings_alarm_alerts_and_status_out_of_band(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized()
    store(repository, incident, events)
    alert = replace(make_alert(service=incident.services[0]), rule_name="Erros na pagamentos-api")
    repository.links.append((incident.id, alert))

    fragment = ana.get(f"/incidents/{incident.id}/timeline").text

    assert 'id="alarm" class="alarm alarm-normalizado" hx-swap-oob="true"' in fragment
    assert 'id="alerts" hx-swap-oob="true"' in fragment
    assert "Erros na pagamentos-api" in fragment
    assert 'id="incident-status" hx-swap-oob="true"' in fragment
    assert 'class="record"' not in fragment  # formulários fora da troca


def test_list_shows_the_badge_only_where_the_alarm_stopped(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    quiet, _ = normalized()
    firing = make_incident(firing=frozenset({"fp"}), last_alert_at=NORMALIZED_AT)
    repository.incidents = {quiet.id: quiet, firing.id: firing}

    page = client.get("/incidents").text

    assert page.count("alarme normalizado") == 1
