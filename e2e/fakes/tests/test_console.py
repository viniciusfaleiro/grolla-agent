import json
from typing import Any

import httpx2
from fastapi.testclient import TestClient
from grolla_fakes.app import create_app
from grolla_fakes.console import ApiTarget

FORM = {
    "alertname": "Taxa de erro alta",
    "service": "captura-api",
    "severity": "critical",
    "team": "credenciadora",
    "summary": "erro de 7%",
    "description": "taxa de erro em 7%",
}


class FakeApi:
    """Faz o papel do webhook da api: grava o que recebeu e responde o status programado."""

    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.received: list[tuple[str, Any]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.received.append((request.headers["authorization"], json.loads(request.content)))
        return httpx2.Response(self.status, json={"status": "accepted"})


def console(api: FakeApi) -> TestClient:
    target = ApiTarget(url="http://api", transport=httpx2.MockTransport(api.handle))
    return TestClient(create_app(target))


def test_page_opens_with_teams_accepting() -> None:
    client = console(FakeApi())

    page = client.get("/_console")

    assert page.status_code == 200
    assert "console de testes" in page.text
    assert client.get("/_console/state").json()["teams_status"] == 202


def test_page_offers_the_llm_scripts_through_the_control_route() -> None:
    page = console(FakeApi()).get("/_console").text

    assert '"/_control/llm"' in page
    for mode in ("completa", "fora_do_ar", "lenta"):
        assert f'data-llm="{mode}"' in page


def test_fired_alert_reaches_the_webhook_in_the_alerting_format() -> None:
    api = FakeApi()
    client = console(api)

    result = client.post("/_console/alerts", json=FORM).json()

    assert result["api_status"] == 202
    [(authorization, payload)] = api.received
    assert authorization.startswith("Basic ")
    [alert] = payload["alerts"]
    assert alert["status"] == "firing"
    assert alert["endsAt"] == "0001-01-01T00:00:00Z"
    assert alert["labels"] == {
        "alertname": "Taxa de erro alta",
        "service_name": "captura-api",
        "severity": "critical",
        "team": "credenciadora",
    }
    assert alert["annotations"] == {"summary": "erro de 7%", "description": "taxa de erro em 7%"}


def test_resolve_resends_the_same_alert_as_resolved() -> None:
    api = FakeApi()
    client = console(api)
    client.post("/_console/alerts", json=FORM)
    [fired] = client.get("/_console/state").json()["alerts"]

    client.post(f"/_console/alerts/{fired['fingerprint']}/resolve")

    first, second = (payload["alerts"][0] for _, payload in api.received)
    assert second["status"] == "resolved"
    assert (second["fingerprint"], second["startsAt"]) == (first["fingerprint"], first["startsAt"])
    assert second["endsAt"] != "0001-01-01T00:00:00Z"
    [alert] = client.get("/_console/state").json()["alerts"]
    assert alert["status"] == "resolved"


def test_resolving_an_unknown_alert_is_404() -> None:
    assert console(FakeApi()).post("/_console/alerts/nao-existe/resolve").status_code == 404


def test_api_refusal_is_reported() -> None:
    result = console(FakeApi(status=401)).post("/_console/alerts", json=FORM).json()

    assert result["api_status"] == 401


def test_api_down_is_502() -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusado", request=request)

    target = ApiTarget(url="http://api", transport=httpx2.MockTransport(down))
    client = TestClient(create_app(target))

    assert client.post("/_console/alerts", json=FORM).status_code == 502


def test_channel_shows_cards_with_the_status_the_teams_answered() -> None:
    client = console(FakeApi())
    client.get("/_console")
    client.post("/_console/teams", json={"status": 500})
    client.post("/teams/webhook", json={"type": "message", "n": 1})
    client.post("/_console/teams", json={"status": 202})
    client.post("/teams/webhook", json={"type": "message", "n": 1})
    client.post("/outra/rota", json={})

    state = client.get("/_console/state").json()

    assert state["teams_status"] == 202
    assert [m["status"] for m in state["messages"]] == [500, 202]


def test_reset_also_clears_the_console() -> None:
    client = console(FakeApi())
    client.post("/_console/alerts", json=FORM)

    client.post("/_control/reset")

    assert client.get("/_console/state").json()["alerts"] == []
