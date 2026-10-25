from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.incidents import EventType, IncidentEvent, Severity
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(create_app(replace(services, repository=repository)))


def test_list_shows_the_most_recent_incident_first(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    older = make_incident(services=("captura",), detected_at=T0)
    newer = make_incident(services=("autorizador",), detected_at=T0 + timedelta(hours=1))
    repository.incidents = {older.id: older, newer.id: newer}

    response = client.get("/incidents")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert f'href="/incidents/{newer.id}"' in page
    assert page.index("autorizador") < page.index("captura")


def test_empty_list_says_there_is_nothing(client: TestClient) -> None:
    response = client.get("/incidents")

    assert response.status_code == 200
    assert "Nenhum incidente registrado" in response.text


def test_incident_page_shows_alerts_and_timeline_in_order(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(severity=Severity.P1, services=("captura",))
    first = make_alert(service="captura", summary="Erro em 7%")
    second = make_alert(service="captura", summary="Latência p99 em 2s", source_url=None)
    repository.incidents = {incident.id: incident}
    repository.links = [(incident.id, first), (incident.id, second)]
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {"fingerprint": "a"}),
        IncidentEvent(incident.id, T0 + timedelta(minutes=1), EventType.ALERT_GROUPED, {}),
        IncidentEvent(incident.id, T0 + timedelta(minutes=2), EventType.SEVERITY_CHANGED, {}),
    ]

    response = client.get(f"/incidents/{incident.id}")

    assert response.status_code == 200
    page = response.text
    assert "P1" in page
    assert "Erro em 7%" in page
    assert "Latência p99 em 2s" in page
    assert 'href="https://observabilidade.interno/alerta/1"' in page
    received = page.index("Alerta recebido")
    assert received < page.index("Alerta agrupado") < page.index("Severidade alterada")


def test_page_escapes_alert_text(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.links = [(incident.id, make_alert(summary="<script>x</script>"))]

    page = client.get(f"/incidents/{incident.id}").text

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


@pytest.mark.parametrize("incident_id", [str(uuid4()), "nao-e-um-uuid"])
def test_unknown_incident_is_not_found(client: TestClient, incident_id: str) -> None:
    assert client.get(f"/incidents/{incident_id}").status_code == 404
