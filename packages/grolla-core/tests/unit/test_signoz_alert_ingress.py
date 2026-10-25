import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from grolla.adapters.signoz import SignozAlertIngress
from grolla.application.incidents import InvalidAlertPayloadError
from grolla.domain.incidents import UNKNOWN_SERVICE, AlertStatus

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SERVICE_LABELS = ("service_name", "service.name")


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def basic(credentials: str) -> dict[str, str]:
    return {"authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}


@pytest.fixture
def ingress() -> SignozAlertIngress:
    return SignozAlertIngress("grolla", "s3nha", SERVICE_LABELS)


def test_accepts_the_configured_credentials(ingress: SignozAlertIngress) -> None:
    assert ingress.authenticate(basic("grolla:s3nha"))


def test_header_name_is_case_insensitive(ingress: SignozAlertIngress) -> None:
    headers = {"Authorization": basic("grolla:s3nha")["authorization"]}
    assert ingress.authenticate(headers)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        basic("grolla:errada"),
        basic("outro:s3nha"),
        basic("grolla"),
        {"authorization": "Bearer abc"},
        {"authorization": "Basic"},
        {"authorization": "Basic não-é-base64!"},
        {"authorization": "Basic " + base64.b64encode(b"\xff\xfe").decode()},
    ],
)
def test_refuses_missing_wrong_or_malformed_credentials(
    ingress: SignozAlertIngress, headers: dict[str, str]
) -> None:
    assert not ingress.authenticate(headers)


def test_parses_the_firing_fixture(ingress: SignozAlertIngress) -> None:
    payload = fixture("signoz_firing.json")

    alerts = ingress.parse(payload)

    assert len(alerts) == 2
    first = alerts[0]
    assert first.fingerprint == "a1b2c3d4e5f60718"
    assert first.status is AlertStatus.FIRING
    assert first.rule_name == "Taxa de erro alta no authorizer"
    assert first.service == "authorizer-api"
    assert first.labels["team"] == "credenciadora"
    assert first.summary == "Taxa de erro de 7,2% no authorizer-api"
    assert first.description.startswith("A taxa de erro HTTP 5xx")
    assert first.starts_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert first.ends_at is None
    assert first.source_url == "https://signoz.interno/alerts/edit?ruleId=42"
    assert first.raw == payload["alerts"][0]


def test_parses_the_resolved_fixture(ingress: SignozAlertIngress) -> None:
    [alert] = ingress.parse(fixture("signoz_resolved.json"))

    assert alert.status is AlertStatus.RESOLVED
    assert alert.ends_at == datetime(2026, 1, 1, 10, 20, tzinfo=UTC)


def test_service_comes_from_the_first_configured_label_present() -> None:
    payload = fixture("signoz_firing.json")
    payload["alerts"][0]["labels"] |= {"app": "captura"}

    alert = SignozAlertIngress("u", "p", ["app", "service_name"]).parse(payload)[0]

    assert alert.service == "captura"


def test_alert_without_service_label_is_unknown(ingress: SignozAlertIngress) -> None:
    payload = fixture("signoz_firing.json")
    del payload["alerts"][0]["labels"]["service_name"]

    assert ingress.parse(payload)[0].service == UNKNOWN_SERVICE


def test_optional_fields_may_be_missing(ingress: SignozAlertIngress) -> None:
    payload: dict[str, Any] = {
        "alerts": [
            {
                "status": "firing",
                "labels": {},
                "startsAt": "2026-01-01T07:00:00-03:00",
                "fingerprint": "f",
            }
        ]
    }

    [alert] = ingress.parse(payload)

    assert alert.rule_name == ""
    assert alert.summary == ""
    assert alert.description == ""
    assert alert.ends_at is None
    assert alert.source_url is None
    assert alert.starts_at == datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    assert alert.starts_at.utcoffset() is not None
    assert alert.starts_at.tzinfo is UTC


def _without(field: str) -> dict[str, Any]:
    payload = fixture("signoz_firing.json")
    del payload["alerts"][0][field]
    return payload


def _with(field: str, value: Any) -> dict[str, Any]:
    payload = fixture("signoz_firing.json")
    payload["alerts"][0][field] = value
    return payload


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "texto",
        {},
        {"alerts": "não é lista"},
        {"alerts": [42]},
        _without("fingerprint"),
        _without("startsAt"),
        _with("status", "pending"),
        _with("labels", {"severity": 1}),
        _with("startsAt", "ontem"),
        _with("startsAt", "2026-01-01T10:00:00"),
    ],
)
def test_refuses_payload_out_of_format(ingress: SignozAlertIngress, payload: Any) -> None:
    with pytest.raises(InvalidAlertPayloadError):
        ingress.parse(payload)
