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


def test_alarm_firing_since_the_first_alert_while_it_never_normalized() -> None:
    incident = make_incident(firing=frozenset({"fp"}))
    events = [
        event(incident, EventType.ALERT_RECEIVED, 2),
        event(incident, EventType.ALERT_GROUPED, 4),
    ]

    assert alarm_of(incident, events) == Alarm(AlarmKind.FIRING, T0 + timedelta(minutes=2))


def test_firing_banner_lists_the_firing_alerts_with_their_link(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(firing=frozenset({"fp-1"}))
    firing = replace(
        make_alert(fingerprint="fp-1"),
        rule_name="Latência alta na estoque-api",
        source_url="https://origem/alerta/1",
    )
    resolved = replace(make_alert(fingerprint="fp-2"), rule_name="Erros na estoque-api")
    store(repository, incident, [event(incident, EventType.ALERT_RECEIVED, 0)])
    repository.links += [(incident.id, firing), (incident.id, resolved)]

    banner = client.get(f"/incidents/{incident.id}").text.split('id="alarm"')[1].split("</ul>")[0]

    assert 'data-alarm="disparando"' in banner
    assert "Alarme disparando</strong> desde" in banner
    assert 'Latência alta na estoque-api · <a href="https://origem/alerta/1"' in banner
    assert "abrir o alerta" in banner
    assert "Erros na estoque-api" not in banner


def test_firing_banner_shows_three_alerts_and_how_many_more(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    prints = [f"fp-{n}" for n in range(5)]
    incident = make_incident(firing=frozenset(prints))
    store(repository, incident, [event(incident, EventType.ALERT_RECEIVED, 0)])
    repository.links += [(incident.id, make_alert(fingerprint=fp)) for fp in prints]

    page = client.get(f"/incidents/{incident.id}").text

    assert "e mais 2" in page


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


def test_page_shows_the_normalized_banner_without_buttons(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized(IncidentState.MITIGATED)
    store(repository, incident, events)

    page = ana.get(f"/incidents/{incident.id}").text
    banner = page.split('id="alarm"')[1].split("</div>")[0]

    assert 'data-alarm="normalizado"' in banner
    assert "Alertas normalizados</strong> às" in banner
    assert "há 5 min" in banner
    assert "<button" not in banner  # as ações ficam no painel de ações
    assert 'popovertarget="encerrar"' in page


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

    status = fragment.split('id="incident-status" hx-swap-oob="true"')[1]
    assert 'id="alarm" class="alarm alarm-normalizado"' in status  # a faixa vem no bloco de estado
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


def test_banner_shows_an_alert_received_many_times_once(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(firing=frozenset({"fp-1"}))
    store(repository, incident, [event(incident, EventType.ALERT_RECEIVED, 0)])
    alert = replace(make_alert(fingerprint="fp-1"), rule_name="Latência alta na estoque-api")
    repository.links += [(incident.id, alert)] * 3  # disparou, resolveu e voltou

    banner = client.get(f"/incidents/{incident.id}").text.split('id="alarm"')[1].split("</ul>")[0]

    assert banner.count("Latência alta na estoque-api") == 1


def test_polling_brings_the_buttons_of_the_new_state(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized(IncidentState.HANDLING)  # o alarme voltou e reabriu o mitigado
    store(
        repository,
        replace(incident, firing=frozenset({"fp"})),
        [*events, event(incident, EventType.ALERT_RECEIVED, 18)],
    )

    fragment = ana.get(f"/incidents/{incident.id}/timeline").text
    buttons = fragment.split('id="next-step-main" hx-swap-oob="true"')[1].split("</div>")[0]

    assert "Marcar mitigado" in buttons
    assert "Reabrir" not in buttons
    assert 'class="record"' not in fragment  # o campo de texto fica fora da troca


def test_polling_without_a_name_brings_no_buttons(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident, events = normalized()
    store(repository, incident, events)

    assert "next-step-main" not in client.get(f"/incidents/{incident.id}/timeline").text
