import json
from typing import Any
from uuid import UUID

import httpx2
import pytest
from contracts.chat_notifier_contract import ChatNotifierContract
from grolla.adapters.teams import TeamsChatNotifier
from grolla.application.incidents import ChatNotifier, NotificationFailedError
from grolla.domain.incidents import Notification, NotificationKind, Severity

URL = "https://chat.interno/workflows/abc?sig=segredo"


def notification(
    kind: NotificationKind = NotificationKind.INCIDENT_OPENED,
    source_url: str | None = "https://origem/alerta/42",
) -> Notification:
    return Notification(
        kind=kind,
        incident_id=UUID(int=1),
        title="Taxa de erro alta no capture-api",
        severity=Severity.P2,
        services=("capture-api", "authorizer-api"),
        summary="Taxa de erro de 7,2%",
        evidence="A taxa de erro HTTP 5xx está em 7,2% (limite 2%).",
        source_url=source_url,
        page_url="https://grolla/incidents/1",
    )


class Recorder:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status)

    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


class TestTeamsChatNotifierContract(ChatNotifierContract):
    @pytest.fixture
    def notifier(self) -> ChatNotifier:
        return TeamsChatNotifier(URL, httpx2.MockTransport(Recorder()))

    @pytest.fixture
    def refusing_notifier(self) -> ChatNotifier:
        return TeamsChatNotifier(URL, httpx2.MockTransport(Recorder(500)))


def card_text(body: dict[str, Any]) -> str:
    return json.dumps(body["attachments"][0]["content"], ensure_ascii=False)


async def test_posts_an_adaptive_card_message_to_the_webhook() -> None:
    recorder = Recorder()
    notifier = TeamsChatNotifier(URL, httpx2.MockTransport(recorder))

    await notifier.post(notification())

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    body = recorder.body()
    assert body["type"] == "message"
    [attachment] = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"


async def test_new_incident_card_has_everything_the_spec_asks_for() -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification())

    text = card_text(recorder.body())
    for expected in (
        "Incidente novo",
        "Taxa de erro alta no capture-api",
        "P2",
        "capture-api, authorizer-api",
        "Taxa de erro de 7,2%",
        "A taxa de erro HTTP 5xx está em 7,2% (limite 2%).",
        "https://grolla/incidents/1",
        "https://origem/alerta/42",
    ):
        assert expected in text


@pytest.mark.parametrize(
    ("kind", "headline"),
    [
        (NotificationKind.SEVERITY_RAISED, "Severidade elevada"),
        (NotificationKind.INCIDENT_NORMALIZED, "Incidente normalizado"),
    ],
)
async def test_each_kind_has_its_headline(kind: NotificationKind, headline: str) -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification(kind))

    assert headline in card_text(recorder.body())


async def test_card_without_source_link_only_links_the_page() -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification(source_url=None))

    actions = recorder.body()["attachments"][0]["content"]["actions"]
    assert [action["url"] for action in actions] == ["https://grolla/incidents/1"]


@pytest.mark.parametrize("status", [400, 429, 500, 503])
async def test_error_status_fails_without_leaking_the_url(status: int) -> None:
    notifier = TeamsChatNotifier(URL, httpx2.MockTransport(Recorder(status)))

    with pytest.raises(NotificationFailedError, match=str(status)) as error:
        await notifier.post(notification())

    assert "segredo" not in str(error.value)


@pytest.mark.parametrize("error", [httpx2.ConnectError("recusada"), httpx2.ReadTimeout("lento")])
async def test_network_error_fails(error: Exception) -> None:
    def explode(_request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(NotificationFailedError, match="rede"):
        await TeamsChatNotifier(URL, httpx2.MockTransport(explode)).post(notification())
