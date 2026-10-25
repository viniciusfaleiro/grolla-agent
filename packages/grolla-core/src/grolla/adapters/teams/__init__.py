"""Microsoft Teams: Adaptive Cards postados por um Workflow (webhook) como ChatNotifier."""

from typing import Any

import httpx2

from grolla.application.incidents import NotificationFailedError
from grolla.domain.analysis import (
    INITIAL_CHECKS,
    Analysis,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
)
from grolla.domain.incidents import Notification, NotificationKind

TIMEOUT_SECONDS = 10.0

_HEADLINES = {
    NotificationKind.INCIDENT_OPENED: "Estou investigando",
    NotificationKind.ANALYSIS_READY: "Análise pronta",
    NotificationKind.SEVERITY_RAISED: "Severidade elevada",
    NotificationKind.INCIDENT_NORMALIZED: "Incidente normalizado",
}

_COLORS = {
    NotificationKind.INCIDENT_OPENED: "Attention",
    NotificationKind.ANALYSIS_READY: "Accent",
    NotificationKind.SEVERITY_RAISED: "Warning",
    NotificationKind.INCIDENT_NORMALIZED: "Good",
}


_PARTIAL_REASONS = {
    PartialReason.LLM_FAILED: "LLM indisponível",
    PartialReason.TIMEOUT: "tempo esgotado",
    PartialReason.BUDGET: "orçamento esgotado",
    PartialReason.INVALID_RESULT: "resultado inválido",
    PartialReason.INTERRUPTED: "interrompida",
}

_NOTHING = "- nenhuma consulta concluída"


class TeamsChatNotifier:
    def __init__(
        self, webhook_url: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._webhook_url = webhook_url
        self._transport = transport  # testes injetam um transporte fake

    async def post(self, notification: Notification) -> None:
        # As mensagens de erro não citam a URL: ela carrega a assinatura do Workflow.
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(self._webhook_url, json=message(notification))
        except httpx2.HTTPError as error:
            raise NotificationFailedError(f"falha de rede: {type(error).__name__}") from error
        if response.status_code >= 400:  # noqa: PLR2004 — faixa de erro do HTTP
            raise NotificationFailedError(f"canal respondeu HTTP {response.status_code}")


def message(notification: Notification) -> dict[str, Any]:
    """Mensagem no formato que o Workflow do Teams espera, com o Adaptive Card dentro."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card(notification),
            }
        ],
    }


def adaptive_card(notification: Notification) -> dict[str, Any]:
    analysis = notification.analysis
    if notification.kind is NotificationKind.ANALYSIS_READY and analysis is not None:
        body = _analysis_body(notification, analysis)
        source_title = "Ver no SigNoz"
    else:
        body = _alert_body(notification)
        source_title = "Ver alerta na origem"
    actions = [{"type": "Action.OpenUrl", "title": "Abrir incidente", "url": notification.page_url}]
    if notification.source_url:
        actions.append(
            {"type": "Action.OpenUrl", "title": source_title, "url": notification.source_url}
        )
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
        "actions": actions,
    }


def _alert_body(notification: Notification) -> list[dict[str, Any]]:
    kind = notification.kind
    body = [
        _headline(_HEADLINES[kind], notification, _COLORS[kind]),
        *_identity(notification),
        _text(notification.summary),
        {"type": "TextBlock", "text": notification.evidence, "wrap": True, "isSubtle": True},
    ]
    if kind is NotificationKind.INCIDENT_OPENED:
        body.append(_section("Vou checar", [f"- {check}" for check in INITIAL_CHECKS]))
    return body


def _analysis_body(notification: Notification, analysis: Analysis) -> list[dict[str, Any]]:
    if analysis.partial_reason is None:
        body = [_headline("Análise pronta", notification, "Accent")]
    else:
        body = [
            _headline("Análise parcial", notification, "Warning"),
            _text(f"Motivo: {_PARTIAL_REASONS[analysis.partial_reason]}"),
        ]
    body.extend(_identity(notification))
    if analysis.summary:
        body.append(_text(analysis.summary))
    body.append(
        _section("O que avaliei", [_evaluation(e) for e in analysis.evaluations] or [_NOTHING])
    )
    if analysis.hypotheses:
        body.append(_section("Hipóteses", [_hypothesis(h) for h in analysis.hypotheses]))
    if analysis.plan:
        body.append(_section("Plano proposto", [f"- {step}" for step in analysis.plan]))
    return body


def _headline(text: str, notification: Notification, color: str) -> dict[str, Any]:
    return {
        "type": "TextBlock",
        "text": f"{text} · {notification.severity}",
        "weight": "Bolder",
        "color": color,
        "size": "Medium",
    }


def _identity(notification: Notification) -> list[dict[str, Any]]:
    return [
        {"type": "TextBlock", "text": notification.title, "weight": "Bolder", "wrap": True},
        {
            "type": "FactSet",
            "facts": [
                {"title": "Severidade", "value": str(notification.severity)},
                {"title": "Serviços", "value": ", ".join(notification.services)},
            ],
        },
    ]


def _text(text: str) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True}


def _section(title: str, lines: list[str]) -> dict[str, Any]:
    return {
        "type": "Container",
        "separator": True,
        "items": [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
            _text("\n".join(lines)),
        ],
    }


def _evaluation(evaluation: Evaluation) -> str:
    line = f"- {evaluation.subject}: {evaluation.finding}"
    return f"{line} ([abrir]({evaluation.url}))" if evaluation.url else line


def _hypothesis(hypothesis: Hypothesis) -> str:
    line = f"- {hypothesis.title} (confiança {hypothesis.confidence:.0%})"
    evidence = "; ".join(_evidence(e) for e in hypothesis.evidence)
    return f"{line}: {evidence}" if evidence else line


def _evidence(evidence: Evidence) -> str:
    return f"[{evidence.text}]({evidence.url})" if evidence.url else evidence.text
