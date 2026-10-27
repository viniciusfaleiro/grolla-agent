from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.application.investigation import Signal, ToolUnavailableError
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.console import ChartRequest, ConsoleMessage
from grolla.domain.incidents import EventType, Severity
from grolla.testing.clock import FakeClock
from grolla.testing.console import InMemoryConsoleStore
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import FakeTelemetry
from grolla_api.app import Services, create_app
from grolla_api.console_pages import CONSOLE_SUGGESTIONS, console_suggestions
from grolla_api.war_room import AUTHOR_COOKIE

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
HTMX = {"HX-Request": "true"}
LATENCY = ChartRequest("pedidos-api", "latencia_p99", 60)
ERRORS = ChartRequest("pedidos-api", "taxa_de_erro", 30)


class ChartTelemetry(FakeTelemetry):
    """Só a série dos gráficos importa aqui; `down` simula a telemetria fora do ar."""

    def __init__(self, down: bool = False) -> None:
        super().__init__()
        self.down = down
        self.asked: list[tuple[str, str, datetime, datetime]] = []

    async def series(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> list[tuple[datetime, float]]:
        self.asked.append((service, signal, start, end))
        if self.down:
            raise ToolUnavailableError("fora do ar")
        return [
            (end - timedelta(minutes=2), 120.0),
            (end - timedelta(minutes=1), 340.0),
            (end, 200.0),
        ]


@pytest.fixture
def store() -> InMemoryConsoleStore:
    return InMemoryConsoleStore()


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def telemetry() -> ChartTelemetry:
    return ChartTelemetry()


@pytest.fixture
def client(
    services: Services,
    store: InMemoryConsoleStore,
    repository: InMemoryIncidentRepository,
    telemetry: ChartTelemetry,
) -> TestClient:
    app = create_app(
        replace(
            services,
            console=store,
            telemetry=telemetry,
            repository=repository,
            clock=FakeClock(NOW),
        )
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def new_session(ana: TestClient, text: str = "o autorizador está saudável?") -> str:
    response = ana.post("/console", data={"texto": text})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/console/")
    return location.removeprefix("/console/")


async def reply(
    store: InMemoryConsoleStore, text: str, charts: tuple[ChartRequest, ...] = ()
) -> None:
    """O worker responde a pergunta pendente."""
    job = await store.claim(NOW)
    assert job is not None
    answer = ConsoleMessage(
        id=0,
        session_id=job.session_id,
        author=AGENT_AUTHOR,
        text=text,
        at=NOW,
        charts=charts,
        reply_to=job.message_id,
    )
    await store.answer(job, answer, [], NOW)


def test_console_off_says_so(services: Services) -> None:
    client = TestClient(create_app(replace(services, console=None)), follow_redirects=False)
    response = client.get("/console")
    assert response.status_code == 200
    assert "O console está desligado nesta instância" in response.text
    assert client.post("/console", data={"texto": "oi"}).status_code == 404
    assert client.get(f"/console/{uuid4()}").status_code == 404


def test_navbar_links_the_console(client: TestClient) -> None:
    assert 'href="/console"' in client.get("/incidents").text


def test_empty_console_offers_a_new_conversation(ana: TestClient) -> None:
    page = ana.get("/console").text
    assert "Nova conversa" in page
    assert "Nenhuma conversa ainda" in page


def test_new_conversation_creates_the_session_and_redirects(ana: TestClient) -> None:
    session_id = new_session(ana)
    page = ana.get(f"/console/{session_id}").text
    assert "o autorizador está saudável?" in page
    assert "Grolla está investigando" in page
    listing = ana.get("/console").text
    assert f'href="/console/{session_id}"' in listing
    assert "por Ana" in listing


def test_new_conversation_by_htmx_uses_hx_redirect(ana: TestClient) -> None:
    response = ana.post("/console", data={"texto": "oi"}, headers=HTMX)
    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/console/")


def test_new_conversation_needs_a_name_and_a_text(client: TestClient) -> None:
    response = client.post("/console", data={"texto": "oi"})
    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#console-error"
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    assert client.post("/console", data={"texto": "  "}).status_code == 422


def test_unknown_session_is_404(ana: TestClient) -> None:
    assert ana.get(f"/console/{uuid4()}").status_code == 404
    assert ana.get("/console/nao-e-uuid").status_code == 404
    assert ana.post(f"/console/{uuid4()}", data={"texto": "oi"}).status_code == 404


async def test_reply_draws_the_charts_with_real_series(
    ana: TestClient, store: InMemoryConsoleStore, telemetry: ChartTelemetry
) -> None:
    session_id = new_session(ana, "o pedidos-api está lento?")
    await reply(store, "Sim: o **p99** subiu.", (LATENCY, ERRORS))
    page = ana.get(f"/console/{session_id}").text
    assert "<strong>p99</strong>" in page  # rich_text
    assert "Latência p99 do pedidos-api, últimos 60 min" in page
    assert "Taxa de erro do pedidos-api, últimos 30 min" in page
    assert page.count("<svg") >= 2
    assert "ms</text>" in page
    assert "%</text>" in page
    assert "Grolla está investigando" not in page
    service, signal, start, end = telemetry.asked[0]
    assert (service, signal) == ("pedidos-api", "latencia_p99")
    assert end - start == timedelta(minutes=60)


async def test_telemetry_failure_shows_unavailable(
    services: Services, store: InMemoryConsoleStore
) -> None:
    for telemetry in (ChartTelemetry(down=True), None):
        app = create_app(replace(services, console=store, telemetry=telemetry))
        client = TestClient(app, follow_redirects=False)
        client.cookies.set(AUTHOR_COOKIE, "Ana")
        session_id = new_session(client)
        await reply(store, "veja", (LATENCY,))
        page = client.get(f"/console/{session_id}").text
        assert "gráfico indisponível" in page
        assert "Latência p99 do pedidos-api" in page


async def test_polling_returns_204_until_the_reply(
    ana: TestClient, store: InMemoryConsoleStore
) -> None:
    session_id = new_session(ana)
    first = ana.get(f"/console/{session_id}/mensagens")
    assert first.status_code == 200
    seen = first.text.split("visto=")[1].split('"')[0]
    assert ana.get(f"/console/{session_id}/mensagens?visto={seen}").status_code == 204
    await reply(store, "Está saudável.")
    after = ana.get(f"/console/{session_id}/mensagens?visto={seen}")
    assert after.status_code == 200
    assert "Está saudável." in after.text


async def test_follow_up_question_returns_the_conversation(
    ana: TestClient, store: InMemoryConsoleStore
) -> None:
    session_id = new_session(ana)
    await reply(store, "Está saudável.")
    response = ana.post(f"/console/{session_id}", data={"texto": "e ontem?"}, headers=HTMX)
    assert response.status_code == 200
    assert 'id="chat-messages"' in response.text
    assert "e ontem?" in response.text
    assert "Grolla está investigando" in response.text


def test_follow_up_errors(ana: TestClient, client: TestClient) -> None:
    session_id = new_session(ana)
    assert ana.post(f"/console/{session_id}", data={"texto": ""}).status_code == 422
    client.cookies.clear()
    response = client.post(f"/console/{session_id}", data={"texto": "oi"})
    assert response.status_code == 401


async def test_open_incident_from_the_conversation(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    session_id = new_session(ana, "o pedidos-api está lento?")
    assert "Abrir incidente" in ana.get(f"/console/{session_id}").text
    response = ana.post(
        f"/console/{session_id}/incidente",
        data={"titulo": "pedidos-api lento", "servico": "pedidos-api", "severidade": "P2"},
    )
    assert response.status_code == 303
    incident_id = UUID(response.headers["location"].removeprefix("/incidents/"))
    incident = await repository.get_incident(incident_id)
    assert incident is not None
    assert incident.title == "pedidos-api lento"
    assert incident.severity is Severity.P2
    page = ana.get(f"/console/{session_id}").text
    assert f'href="/incidents/{incident_id}"' in page
    assert "pedidos-api lento" in page
    assert f'href="/incidents/{incident_id}"' in ana.get("/console").text
    events = await repository.events_of(incident_id)
    assert any(e.type is EventType.INCIDENT_OPENED_MANUALLY for e in events)
    assert "Aberto pelo console" in ana.get(f"/incidents/{incident_id}").text


def test_open_incident_by_htmx_uses_hx_redirect(ana: TestClient) -> None:
    session_id = new_session(ana)
    response = ana.post(
        f"/console/{session_id}/incidente",
        data={"titulo": "lento", "servico": "pedidos-api", "severidade": "P1"},
        headers=HTMX,
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/incidents/")


def test_open_incident_errors(ana: TestClient, client: TestClient) -> None:
    session_id = new_session(ana)
    url = f"/console/{session_id}/incidente"
    fields = {"titulo": "lento", "servico": "pedidos-api", "severidade": "P9"}
    response = ana.post(url, data=fields)
    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#erro-incidente"
    assert ana.post(url, data={**fields, "severidade": "P2", "titulo": ""}).status_code == 422
    assert ana.post(f"/console/{uuid4()}/incidente", data=fields).status_code == 404
    client.cookies.clear()
    assert client.post(url, data={**fields, "severidade": "P2"}).status_code == 401


def test_session_page_lists_the_conversations_beside_with_this_one_marked(ana: TestClient) -> None:
    first = new_session(ana, "o autorizador está saudável?")
    second = new_session(ana, "o que mudou no pedidos-api?")

    page = ana.get(f"/console/{second}").text

    nav = page.split('class="console-nav"')[1].split("</nav>")[0]
    assert f'href="/console/{first}"' in nav
    assert f'href="/console/{second}" aria-current="page"' in nav
    assert "Abrir incidente" in page
    assert 'class="console-side"' not in page  # o card solitário saiu


def test_suggestions_use_the_known_services() -> None:
    assert console_suggestions([]) == CONSOLE_SUGGESTIONS
    assert console_suggestions(["estoque-api", "pagamentos-api"]) == (
        "A estoque-api está saudável?",
        "O que mudou no pagamentos-api hoje?",
        "Mostra a taxa de erro da estoque-api nas últimas 6 h",
    )
