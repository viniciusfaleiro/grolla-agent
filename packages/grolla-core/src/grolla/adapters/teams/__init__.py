"""Microsoft Teams: Adaptive Cards postados por um Workflow (webhook) como ChatNotifier."""

from typing import Any

import httpx2

from grolla.application.incidents import NotificationFailedError
from grolla.domain.incidents import Notification, NotificationKind

TIMEOUT_SECONDS = 10.0

_HEADLINES = {
    NotificationKind.INCIDENT_OPENED: "Incidente novo",
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
    kind = notification.kind
    actions = [{"type": "Action.OpenUrl", "title": "Abrir incidente", "url": notification.page_url}]
    if notification.source_url:
        actions.append(
            {
                "type": "Action.OpenUrl",
                "title": "Ver alerta na origem",
                "url": notification.source_url,
            }
        )
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": [
            {
                "type": "TextBlock",
                "text": f"{_HEADLINES[kind]} · {notification.severity}",
                "weight": "Bolder",
                "color": _COLORS[kind],
                "size": "Medium",
            },
            {"type": "TextBlock", "text": notification.title, "weight": "Bolder", "wrap": True},
            {
                "type": "FactSet",
                "facts": [
                    {"title": "Severidade", "value": str(notification.severity)},
                    {"title": "Serviços", "value": ", ".join(notification.services)},
                ],
            },
            {"type": "TextBlock", "text": notification.summary, "wrap": True},
            {
                "type": "TextBlock",
                "text": notification.evidence,
                "wrap": True,
                "isSubtle": True,
            },
        ],
        "actions": actions,
    }
