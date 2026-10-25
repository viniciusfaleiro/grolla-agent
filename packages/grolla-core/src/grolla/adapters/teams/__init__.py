"""Microsoft Teams: Adaptive Cards postados por um Workflow (webhook) como ChatNotifier."""

import httpx2

from grolla.domain.incidents import Notification


class TeamsChatNotifier:
    def __init__(
        self, webhook_url: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._webhook_url = webhook_url
        self._transport = transport  # testes injetam um transporte fake

    async def post(self, notification: Notification) -> None:
        raise NotImplementedError
