"""Suíte de contrato de ChatNotifier.

`notifier` aceita as postagens; `refusing_notifier` é o mesmo adapter com o canal recusando
(erro HTTP, rede fora, ...).
"""

from uuid import uuid4

import pytest
from grolla.application.incidents import ChatNotifier, NotificationFailedError
from grolla.domain.incidents import NotificationKind
from grolla.testing.builders import make_notification


class ChatNotifierContract:
    @pytest.fixture
    def notifier(self) -> ChatNotifier:
        raise NotImplementedError

    @pytest.fixture
    def refusing_notifier(self) -> ChatNotifier:
        raise NotImplementedError

    @pytest.mark.parametrize("kind", list(NotificationKind))
    async def test_every_kind_is_posted(
        self, notifier: ChatNotifier, kind: NotificationKind
    ) -> None:
        await notifier.post(make_notification(uuid4(), kind))

    async def test_notification_without_source_link_is_posted(self, notifier: ChatNotifier) -> None:
        await notifier.post(make_notification(uuid4(), source_url=None))

    async def test_refused_post_raises_notification_failed(
        self, refusing_notifier: ChatNotifier
    ) -> None:
        with pytest.raises(NotificationFailedError):
            await refusing_notifier.post(make_notification(uuid4()))
