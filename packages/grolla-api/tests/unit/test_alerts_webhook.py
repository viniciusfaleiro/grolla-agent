from collections.abc import Sequence
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from grolla.domain.incidents import Alert
from grolla.testing.builders import make_alert
from grolla.testing.incidents import FAKE_AUTHORIZATION, FakeAlertIngress
from grolla_api import app as api
from grolla_api.app import Services, create_app

AUTHORIZED = {"Authorization": FAKE_AUTHORIZATION}


@pytest.fixture
def received(monkeypatch: pytest.MonkeyPatch) -> list[Sequence[Alert]]:
    """Troca o caso de uso por um gravador: a api só precisa chamá-lo com os alertas."""
    calls: list[Sequence[Alert]] = []

    async def receive_alerts(alerts: Sequence[Alert], *_: object) -> None:
        calls.append(alerts)

    monkeypatch.setattr(api, "receive_alerts", receive_alerts)
    return calls


@pytest.fixture
def client(services: Services) -> TestClient:
    ingress = FakeAlertIngress(alerts=[make_alert(), make_alert()])
    return TestClient(create_app(replace(services, ingress=ingress)))


def test_valid_payload_is_accepted_and_processed(
    client: TestClient, received: list[Sequence[Alert]]
) -> None:
    response = client.post("/webhooks/alerts", json={"alerts": ["a", "b"]}, headers=AUTHORIZED)

    assert response.status_code == 202
    assert [len(alerts) for alerts in received] == [2]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic c2lnbm96OmVycmFkYQ=="}])
def test_missing_or_wrong_credentials_are_refused_without_effect(
    client: TestClient, received: list[Sequence[Alert]], headers: dict[str, str]
) -> None:
    response = client.post("/webhooks/alerts", json={"alerts": []}, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Basic"
    assert received == []


def test_payload_outside_the_expected_format_is_refused(
    client: TestClient, received: list[Sequence[Alert]]
) -> None:
    response = client.post("/webhooks/alerts", json={"outra": "coisa"}, headers=AUTHORIZED)

    assert response.status_code == 422
    assert received == []


def test_body_that_is_not_json_is_refused(
    client: TestClient, received: list[Sequence[Alert]]
) -> None:
    response = client.post("/webhooks/alerts", content=b"nao e json", headers=AUTHORIZED)

    assert response.status_code == 422
    assert received == []
