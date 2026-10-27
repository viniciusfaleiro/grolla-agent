from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import AnalysisStatus, Verification
from grolla.domain.incidents import IncidentState, Severity
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, IncidentFilter, firing_count, open_card

NOW = T0 + timedelta(hours=40)


def query(**params: str | list[str]) -> IncidentFilter:
    return IncidentFilter.from_query(
        {k: v if isinstance(v, list) else [v] for k, v in params.items()}
    )


def test_filter_reads_the_query_and_ignores_invalid_values() -> None:
    f = query(sev=["P1", "P9"], estado="nada", alarme="x", ic="meus", periodo="7d", q=" pag ")

    assert f.severities == frozenset({Severity.P1})
    assert f.scope == "todos"
    assert f.alarm == ""
    assert f.commander == "meus"
    assert f.period == "7d"
    assert f.text == "pag"
    assert f.active
    assert not IncidentFilter().active


def test_filter_matches_each_criterion() -> None:
    p1 = make_incident(
        severity=Severity.P1, services=("pagamentos-api",), detected_at=NOW - timedelta(hours=2)
    )
    firing = replace(p1, firing=frozenset({"fp"}), commander="Ana")
    old = make_incident(severity=Severity.P3, detected_at=NOW - timedelta(days=10))

    assert query(sev="P1").matches(p1, None, NOW)
    assert not query(sev="P1").matches(old, None, NOW)
    assert query(servico="pagamentos-api").matches(p1, None, NOW)
    assert query(alarme="disparando").matches(firing, None, NOW)
    assert not query(alarme="disparando").matches(p1, None, NOW)
    assert query(ic="meus").matches(firing, "Ana", NOW)
    assert not query(ic="meus").matches(firing, None, NOW)
    assert query(ic="sem").matches(p1, None, NOW)
    assert query(periodo="24h").matches(p1, None, NOW)
    assert not query(periodo="7d").matches(old, None, NOW)
    assert query(q="PAGAMENTOS").matches(p1, None, NOW)


def test_open_card_flags_the_forgotten_and_takes_the_cause() -> None:
    forgotten = make_incident(state=IncidentState.DETECTED, detected_at=NOW - timedelta(hours=38))
    verification = Verification(
        incident_id=forgotten.id,
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        verdicts=(),
        root_cause="antifraude-api com 0 réplicas",
        actions=(),
        missing="",
        evaluations=(),
        partial_reason=None,
    )

    card = open_card(forgotten, None, verification, NOW)

    assert card.forgotten
    assert card.next_step == "Encerrar"
    assert card.cause == "antifraude-api com 0 réplicas"
    assert card.cause_confirmed


def test_firing_count_counts_open_incidents_with_alerts_firing() -> None:
    on = make_incident(firing=frozenset({"fp"}))
    closed = make_incident(state=IncidentState.RESOLVED, firing=frozenset({"fp"}))

    assert firing_count([on, closed, make_incident()]) == 1


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    app = create_app(replace(services, repository=repository, clock=FakeClock(NOW)))
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def test_list_filters_by_service_and_severity(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    wanted = make_incident(severity=Severity.P1, services=("pagamentos-api",))
    other_service = make_incident(severity=Severity.P1, services=("estoque-api",))
    other_sev = make_incident(severity=Severity.P3, services=("pagamentos-api",))
    for i in (wanted, other_service, other_sev):
        repository.incidents[i.id] = replace(i, title=f"Incidente {i.services[0]} {i.severity}")

    page = client.get("/incidents?servico=pagamentos-api&sev=P1").text

    assert "Incidente pagamentos-api P1" in page
    assert "Incidente estoque-api P1" not in page
    assert "Incidente pagamentos-api P3" not in page
    assert "1 de 3 incidentes" in page
    assert 'href="/incidents">Limpar filtros' in page
    assert '<option value="pagamentos-api" selected>' in page


def test_list_shows_the_firing_card_and_the_nav_counter(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    firing = make_incident(firing=frozenset({"fp"}), detected_at=NOW - timedelta(minutes=21))
    repository.incidents[firing.id] = firing

    page = client.get("/incidents").text

    assert "open-card-firing" in page
    assert "Disparando há 21 min" in page
    assert '<span class="nav-count" title="Incidentes com alarme disparando">1</span>' in page
    assert "1 com alarme disparando" in page


def test_closed_table_shows_cause_duration_and_more(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    for n in range(22):
        i = make_incident(state=IncidentState.RESOLVED, detected_at=NOW - timedelta(hours=n + 1))
        repository.incidents[i.id] = replace(i, resolved_at=i.detected_at + timedelta(minutes=14))

    page = client.get("/incidents?estado=encerrados").text

    assert "Precisa de você agora" not in page
    assert page.count('class="cell-title"') == 20
    assert "14 min" in page
    assert "Mostrar mais 2" in page
    assert client.get("/incidents?estado=encerrados&mais=1").text.count('class="cell-title"') == 22


def test_unknown_query_values_are_ignored(client: TestClient) -> None:
    assert client.get(f"/incidents?sev=P9&estado=x&periodo=1y&id={uuid4()}").status_code == 200
