from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from grolla.application.follow_up import ImpactPolicy
from grolla.domain.analysis import PartialReason
from grolla.domain.follow_up import LIMIT_REACHED, Cycle, CycleStatus, Proposal, Trend
from grolla.domain.incidents import (
    ALERT_REOPENED_REASON,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Severity,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, percent, recent_cycles

NOW = T0 + timedelta(minutes=20)
SERVICE = "pedidos-api"


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    impact = ImpactPolicy(per_minute={SERVICE: 1000})
    app = create_app(replace(services, repository=repository, clock=FakeClock(NOW), impact=impact))
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def add_incident(
    repository: InMemoryIncidentRepository,
    state: IncidentState = IncidentState.HANDLING,
    services: tuple[str, ...] = (SERVICE,),
) -> Incident:
    incident = make_incident(severity=Severity.P1, state=state, services=services)
    repository.incidents[incident.id] = incident
    return incident


def cycle(incident: Incident, number: int, **changes: object) -> Cycle:
    finished = T0 + timedelta(minutes=5 * number)
    base = Cycle(
        incident_id=incident.id,
        number=number,
        status=CycleStatus.COMPLETED,
        requested_at=finished,
        started_at=finished,
        finished_at=finished,
        trend=Trend.STABLE,
        error_rate=10.0,
        summary="erros estáveis em 10%",
    )
    return replace(base, **changes)


def add_cycles(repository: InMemoryIncidentRepository, *cycles: Cycle) -> None:
    for item in cycles:
        repository.cycles[item.incident_id, item.number] = item


def proposal_event(incident: Incident, proposal: Proposal, state: IncidentState) -> IncidentEvent:
    data = {"proposal": proposal.value, "state": state.value, "reason": "normalizado há 15 min"}
    return IncidentEvent(incident.id, NOW, EventType.AGENT_PROPOSAL, data, author="agente")


# ---- acompanhamento


def test_follow_up_shows_the_latest_cycles_first_with_trend_rate_and_summary(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    add_cycles(
        repository,
        cycle(incident, 1, trend=Trend.WORSE, error_rate=50.0, summary="erros subindo"),
        cycle(incident, 2, trend=Trend.BETTER, error_rate=12.5, summary="erros caindo"),
        cycle(incident, 3, status=CycleStatus.PARTIAL, partial_reason=PartialReason.TIMEOUT),
        cycle(incident, 4, status=CycleStatus.PENDING, trend=None),
    )

    page = client.get(f"/incidents/{incident.id}").text

    assert "<h2>Acompanhamento</h2>" in page
    assert "ciclo 3" in page
    assert '<span class="trend trend-piorando">piorando</span>' in page
    assert '<span class="trend trend-melhorando">melhorando</span>' in page
    assert "12,5% de erro" in page
    assert "erros caindo" in page
    assert '<span class="trend trend-parcial">parcial</span>' in page
    assert "tempo esgotado" in page
    assert page.index("tempo esgotado") < page.index("erros caindo") < page.index("erros subindo")


def test_incident_without_cycles_has_the_follow_up_hidden(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    page = client.get(f"/incidents/{incident.id}").text

    assert '<section class="panel follow-up" id="acompanhamento" hidden>' in page
    assert "<h2>Acompanhamento</h2>" not in page


def test_recent_cycles_keeps_only_the_finished_ones_newest_first() -> None:
    incident = make_incident()
    cycles = [cycle(incident, n) for n in range(1, 10)]
    cycles.append(cycle(incident, 10, status=CycleStatus.RUNNING))

    assert [c.number for c in recent_cycles(cycles)] == [9, 8, 7, 6, 5, 4]


@pytest.mark.parametrize(
    ("rate", "text"), [(None, ""), (0.0, "0%"), (12.5, "12,5%"), (7.04, "7%"), (100.0, "100%")]
)
def test_percent(rate: float | None, text: str) -> None:
    assert percent(rate) == text


# ---- impacto


def test_estimated_impact_is_in_the_header(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    add_cycles(
        repository,
        cycle(incident, 1, error_rate=50.0),
        cycle(incident, 2, error_rate=10.0),
    )

    page = client.get(f"/incidents/{incident.id}").text

    assert "Impacto: R$ 3.000,00 estimado" in page


def test_no_impact_without_a_value_for_the_service(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, services=("outro-servico",))
    add_cycles(repository, cycle(incident, 1, error_rate=50.0))

    page = client.get(f"/incidents/{incident.id}").text

    assert '<span id="impact" class="impact" hidden' in page
    assert "Impacto:" not in page


# ---- proposta


def test_proposal_to_mitigate_shows_the_reason_and_the_button(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.events = [proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING)]

    page = ana.get(f"/incidents/{incident.id}").text
    notice = page[page.index('id="proposal"') : page.index("</aside>")]

    assert "Grolla sugere marcar como mitigado" in notice
    assert "normalizado há 15 min" in notice
    assert f'hx-post="/incidents/{incident.id}/actions/mitigate"' in notice


def test_proposal_to_resolve_opens_the_reason_popover(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, state=IncidentState.MITIGATED)
    repository.events = [proposal_event(incident, Proposal.RESOLVE, IncidentState.MITIGATED)]

    page = ana.get(f"/incidents/{incident.id}").text
    notice = page[page.index('id="proposal"') : page.index("</aside>")]

    assert "Grolla sugere encerrar" in notice
    assert 'popovertarget="encerrar"' in notice


def test_proposal_without_a_name_has_no_button(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.events = [proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING)]

    page = client.get(f"/incidents/{incident.id}").text

    assert "Grolla sugere marcar como mitigado" in page
    assert "/actions/" not in page


def test_proposal_made_in_another_state_is_gone(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository, state=IncidentState.MITIGATED)
    repository.events = [proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING)]

    page = ana.get(f"/incidents/{incident.id}").text

    assert '<div id="proposal" hidden>' in page
    assert "Grolla sugere marcar como mitigado</p>" not in page


def test_marking_mitigated_from_the_proposal_hides_it(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    repository.events = [proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING)]

    fragment = ana.post(f"/incidents/{incident.id}/actions/mitigate").text

    assert '<div id="proposal" hidden>' in fragment


# ---- timeline


def test_timeline_labels_the_follow_up_events(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    cycle_data = {"number": 1, "trend": "piorando", "error_rate": 50.0, "summary": "erros subindo"}
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.CYCLE_COMPLETED, cycle_data, "agente"),
        IncidentEvent(incident.id, T0, EventType.CYCLE_PARTIAL, {"reason": "tempo_esgotado"}),
        IncidentEvent(incident.id, T0, EventType.CYCLE_PARTIAL, {"reason": None}),
        proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING),
        IncidentEvent(incident.id, T0, EventType.FOLLOW_UP_STOPPED, {"reason": LIMIT_REACHED}),
        IncidentEvent(
            incident.id,
            T0,
            EventType.STATE_CHANGED,
            {"from": "mitigado", "to": "em_tratamento", "reason": ALERT_REOPENED_REASON},
        ),
    ]

    fragment = client.get(f"/incidents/{incident.id}/timeline").text

    assert "Acompanhamento: piorando — erros subindo" in fragment
    assert "Acompanhamento parcial" in fragment
    assert "tempo esgotado" in fragment
    assert "None" not in fragment
    assert "Grolla sugere marcar como mitigado" in fragment
    assert "Acompanhamento parado" in fragment
    assert LIMIT_REACHED in fragment
    assert "Mitigado → Em tratamento: alerta voltou a disparar" in fragment


def test_timeline_polling_brings_the_follow_up_out_of_band(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    add_cycles(repository, cycle(incident, 1, error_rate=50.0, summary="erros estáveis"))
    repository.events = [proposal_event(incident, Proposal.MITIGATE, IncidentState.HANDLING)]

    fragment = ana.get(f"/incidents/{incident.id}/timeline").text

    assert 'id="acompanhamento" hx-swap-oob="true"' in fragment
    assert 'id="proposal" hx-swap-oob="true"' in fragment
    assert 'id="incident-status" hx-swap-oob="true"' in fragment
    assert 'id="impact" class="impact"' in fragment
    assert "erros estáveis" in fragment
    assert f'hx-post="/incidents/{incident.id}/actions/mitigate"' in fragment


def test_the_page_itself_has_no_out_of_band_blocks(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    assert "hx-swap-oob" not in client.get(f"/incidents/{incident.id}").text
