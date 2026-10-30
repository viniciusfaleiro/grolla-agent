import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from grolla.domain.incidents import IncidentState
from grolla.testing.builders import make_incident
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app

FALAS = (
    "Opa! Eu sou o Grolla",
    "Tocou um alarme?",
    "Enquanto vocês chegam",
    "te passo o comando, dizendo onde rodar. Quem aplica é você.",
    "No war room, me chama no chat.",
    "Bora?",
)


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(create_app(replace(services, repository=repository)))


@pytest.mark.parametrize("path", ["/incidents", "/postmortems", "/memoria", "/metricas"])
def test_pages_bring_the_welcome_that_opens_by_itself(client: TestClient, path: str) -> None:
    page = client.get(path).text

    assert '<body data-welcome="auto">' in page
    assert '<dialog class="welcome" id="boas-vindas"' in page
    assert 'id="conhecer-grolla">Conhecer o Grolla</button>' in page
    assert '<script src="/static/welcome.js" defer></script>' in page
    assert page.count("<template data-fala") == len(FALAS)
    falas = re.findall(r"<p data-texto>(.*?)</p>", page)
    assert all(trecho in fala for trecho, fala in zip(FALAS, falas, strict=True))


def test_the_command_scene_shows_where_to_run(client: TestClient) -> None:
    page = client.get("/incidents").text

    scene = page.split("Quem aplica é você.</p>", 1)[1].split("</template>", 1)[0]
    assert "Onde rodar: <strong>cluster lab-k3s, namespace loja</strong>" in scene
    assert '<div class="command"><code>kubectl scale deployment antifraude-api' in scene
    assert "Apliquei" in scene


@pytest.mark.parametrize(
    ("state", "mode"),
    [
        (IncidentState.DETECTED, "manual"),
        (IncidentState.HANDLING, "manual"),
        (IncidentState.MITIGATED, "manual"),
        (IncidentState.RESOLVED, "auto"),
        (IncidentState.DISCARDED, "auto"),
    ],
)
def test_the_war_room_of_an_open_incident_does_not_open_it_by_itself(
    client: TestClient, repository: InMemoryIncidentRepository, state: IncidentState, mode: str
) -> None:
    incident = make_incident(state=state)
    repository.incidents[incident.id] = incident

    page = client.get(f"/incidents/{incident.id}").text

    assert f'<body data-welcome="{mode}">' in page
    assert 'id="conhecer-grolla">Conhecer o Grolla</button>' in page
