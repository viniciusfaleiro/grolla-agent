"""SigNoz: webhook de alertas (formato compatível com Alertmanager) como AlertIngress."""

from collections.abc import Mapping, Sequence
from typing import Any

from grolla.domain.incidents import Alert


class SignozAlertIngress:
    def __init__(self, username: str, password: str, service_labels: Sequence[str]) -> None:
        self._username = username
        self._password = password
        self._service_labels = tuple(service_labels)

    def authenticate(self, headers: Mapping[str, str]) -> bool:
        raise NotImplementedError

    def parse(self, payload: Any) -> list[Alert]:
        raise NotImplementedError
