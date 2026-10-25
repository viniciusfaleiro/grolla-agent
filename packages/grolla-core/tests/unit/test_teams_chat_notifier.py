import json
from dataclasses import replace
from typing import Any
from uuid import UUID

import httpx2
import pytest
from contracts.chat_notifier_contract import ChatNotifierContract
from grolla.adapters.teams import TeamsChatNotifier, adaptive_card
from grolla.application.incidents import ChatNotifier, NotificationFailedError
from grolla.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
)
from grolla.domain.incidents import Notification, NotificationKind, Severity
from grolla.testing.builders import T0

URL = "https://chat.interno/workflows/abc?sig=segredo"


def notification(
    kind: NotificationKind = NotificationKind.INCIDENT_OPENED,
    source_url: str | None = "https://origem/alerta/42",
    analysis: Analysis | None = None,
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
        analysis=analysis,
    )


EVALUATIONS = (
    Evaluation(
        tool="query_metric",
        subject="taxa_de_erro de capture-api, últimos 60 min",
        finding="subiu de 0,3% para 7,2% às 10:04",
        url="https://signoz/metricas/1",
        ok=True,
    ),
    Evaluation(
        tool="search_logs",
        subject="logs de erro de capture-api",
        finding="não foi possível consultar",
        url=None,
        ok=False,
    ),
)


def analysis(reason: PartialReason | None = None) -> Analysis:
    return Analysis(
        incident_id=UUID(int=1),
        status=AnalysisStatus.COMPLETED if reason is None else AnalysisStatus.PARTIAL,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        summary="Análise parcial." if reason else "Erros começaram logo depois do deploy 1.4.2.",
        evaluations=EVALUATIONS,
        hypotheses=()
        if reason
        else (
            Hypothesis(
                title="Deploy 1.4.2 com regressão",
                confidence=0.8,
                evidence=(
                    Evidence("build 1.4.2 às 10:02", "https://devops/build/7"),
                    Evidence("erro começou às 10:04", None),
                ),
            ),
            Hypothesis(title="Banco lento", confidence=0.15, evidence=()),
        ),
        plan=() if reason else ("Comparar erro antes e depois do deploy", "Avaliar rollback"),
        partial_reason=reason,
    )


def analysis_card(item: Analysis, source_url: str | None = "https://signoz") -> dict[str, Any]:
    return adaptive_card(notification(NotificationKind.ANALYSIS_READY, source_url, item))


def texts(card: dict[str, Any]) -> list[str]:
    """Os textos do card em ordem, descendo nos containers."""
    found: list[str] = []
    for element in card["body"]:
        for item in element.get("items", [element]):
            if item["type"] == "TextBlock":
                found.append(item["text"])
            elif item["type"] == "FactSet":
                found.extend(f"{fact['title']}: {fact['value']}" for fact in item["facts"])
    return found


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
        "Estou investigando · P2",
        "Vou checar",
        "métricas do serviço",
        "deploys recentes",
        "traces com erro",
        "logs de erro",
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


def test_completed_analysis_card_shows_evaluations_hypotheses_and_plan() -> None:
    card = analysis_card(analysis())

    lines = texts(card)
    assert lines[0] == "Análise pronta · P2"
    assert "Serviços: capture-api, authorizer-api" in lines
    assert "Erros começaram logo depois do deploy 1.4.2." in lines
    section = {title: lines[i + 1] for i, title in enumerate(lines[:-1])}
    assert section["O que avaliei"] == (
        "- taxa_de_erro de capture-api, últimos 60 min: subiu de 0,3% para 7,2% às 10:04"
        " ([abrir](https://signoz/metricas/1))\n"
        "- logs de erro de capture-api: não foi possível consultar"
    )
    assert section["Hipóteses"] == (
        "- Deploy 1.4.2 com regressão (confiança 80%): "
        "[build 1.4.2 às 10:02](https://devops/build/7); erro começou às 10:04\n"
        "- Banco lento (confiança 15%)"
    )
    assert section["Plano proposto"] == (
        "- Comparar erro antes e depois do deploy\n- Avaliar rollback"
    )


def test_analysis_card_links_the_incident_and_the_telemetry() -> None:
    card = analysis_card(analysis())

    assert [(a["title"], a["url"]) for a in card["actions"]] == [
        ("Abrir incidente", "https://grolla/incidents/1"),
        ("Ver no SigNoz", "https://signoz"),
    ]


def test_analysis_card_without_telemetry_link_only_links_the_page() -> None:
    card = analysis_card(analysis(), source_url=None)

    assert [a["title"] for a in card["actions"]] == ["Abrir incidente"]


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        (PartialReason.LLM_FAILED, "LLM indisponível"),
        (PartialReason.TIMEOUT, "tempo esgotado"),
        (PartialReason.BUDGET, "orçamento de tokens esgotado"),
        (PartialReason.INVALID_RESULT, "resultado inválido"),
        (PartialReason.INTERRUPTED, "interrompida"),
    ],
)
def test_partial_analysis_card_says_why_right_below_the_headline(
    reason: PartialReason, text: str
) -> None:
    lines = texts(analysis_card(analysis(reason)))

    assert lines[0] == "Análise parcial · P2"
    assert lines[1] == f"Motivo: {text}"
    assert "Serviços: capture-api, authorizer-api" in lines
    assert "O que avaliei" in lines
    assert "Análise parcial." not in lines
    assert "Hipóteses" not in lines
    assert "Plano proposto" not in lines


def test_partial_analysis_without_evaluations_says_nothing_was_checked() -> None:
    item = analysis(PartialReason.TIMEOUT)
    empty = replace(item, evaluations=())

    lines = texts(analysis_card(empty))

    assert lines[lines.index("O que avaliei") + 1] == "- nenhuma consulta concluída"


def test_card_uses_only_elements_the_test_console_renders() -> None:
    allowed = {"TextBlock", "FactSet", "Container", "ColumnSet"}
    for card in (adaptive_card(notification()), analysis_card(analysis())):
        types = {e["type"] for e in card["body"]}
        types |= {i["type"] for e in card["body"] for i in e.get("items", [])}
        assert types <= allowed
        assert {a["type"] for a in card["actions"]} == {"Action.OpenUrl"}
