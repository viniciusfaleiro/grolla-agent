from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from contracts.alert_ingress_contract import AlertIngressContract
from contracts.chat_notifier_contract import ChatNotifierContract
from contracts.incident_repository_contract import IncidentRepositoryContract
from contracts.notification_outbox_contract import NotificationOutboxContract
from grolla.application.incidents import (
    AlertIngress,
    ChatNotifier,
    IncidentRepository,
    NotificationFailedError,
    NotificationOutbox,
)
from grolla.testing.builders import make_alert, make_notification
from grolla.testing.incidents import (
    FAKE_AUTHORIZATION,
    FakeAlertIngress,
    FakeChatNotifier,
    InMemoryIncidentRepository,
    InMemoryNotificationOutbox,
)


class TestInMemoryIncidentRepositoryContract(IncidentRepositoryContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()


class TestInMemoryNotificationOutboxContract(NotificationOutboxContract):
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        return InMemoryIncidentRepository()

    @pytest.fixture
    def outbox(self, repository: IncidentRepository) -> NotificationOutbox:
        assert isinstance(repository, InMemoryIncidentRepository)
        return InMemoryNotificationOutbox(repository)


class TestFakeChatNotifierContract(ChatNotifierContract):
    @pytest.fixture
    def notifier(self) -> ChatNotifier:
        return FakeChatNotifier()

    @pytest.fixture
    def refusing_notifier(self) -> ChatNotifier:
        return FakeChatNotifier(failures=1)


class TestFakeAlertIngressContract(AlertIngressContract):
    @pytest.fixture
    def ingress(self) -> AlertIngress:
        return FakeAlertIngress([make_alert()])

    @pytest.fixture
    def valid_payload(self) -> Any:
        return {"alerts": []}

    @pytest.fixture
    def invalid_payload(self) -> Any:
        return {"sem": "alertas"}

    @pytest.fixture
    def valid_headers(self) -> Mapping[str, str]:
        return {"authorization": FAKE_AUTHORIZATION}

    @pytest.fixture
    def invalid_headers(self) -> Mapping[str, str]:
        return {"authorization": "Basic errado"}


async def test_fake_notifier_records_and_fails_the_programmed_times() -> None:
    notifier = FakeChatNotifier(failures=2)
    notification = make_notification(uuid4())

    for _ in range(2):
        with pytest.raises(NotificationFailedError):
            await notifier.post(notification)
    await notifier.post(notification)

    assert notifier.attempts == [notification] * 3
    assert notifier.posted == [notification]
