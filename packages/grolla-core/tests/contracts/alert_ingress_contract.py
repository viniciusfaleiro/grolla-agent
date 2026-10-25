"""Suíte de contrato de AlertIngress.

O adapter fornece, pelas fixtures, um payload válido (com ao menos um alerta disparando), um
inválido e cabeçalhos com e sem a credencial certa.
"""

from collections.abc import Mapping
from typing import Any

import pytest
from grolla.application.incidents import AlertIngress, InvalidAlertPayloadError
from grolla.domain.incidents import AlertStatus


class AlertIngressContract:
    @pytest.fixture
    def ingress(self) -> AlertIngress:
        raise NotImplementedError

    @pytest.fixture
    def valid_payload(self) -> Any:
        raise NotImplementedError

    @pytest.fixture
    def invalid_payload(self) -> Any:
        raise NotImplementedError

    @pytest.fixture
    def valid_headers(self) -> Mapping[str, str]:
        raise NotImplementedError

    @pytest.fixture
    def invalid_headers(self) -> Mapping[str, str]:
        raise NotImplementedError

    def test_valid_credentials_authenticate(
        self, ingress: AlertIngress, valid_headers: Mapping[str, str]
    ) -> None:
        assert ingress.authenticate(valid_headers)

    def test_wrong_or_missing_credentials_are_refused(
        self, ingress: AlertIngress, invalid_headers: Mapping[str, str]
    ) -> None:
        assert not ingress.authenticate(invalid_headers)
        assert not ingress.authenticate({})

    def test_valid_payload_becomes_neutral_alerts_keeping_the_raw(
        self, ingress: AlertIngress, valid_payload: Any
    ) -> None:
        alerts = ingress.parse(valid_payload)

        assert alerts
        assert any(alert.status is AlertStatus.FIRING for alert in alerts)
        for alert in alerts:
            assert alert.fingerprint
            assert alert.service
            assert alert.raw
            assert alert.starts_at.utcoffset() is not None

    def test_invalid_payload_raises(self, ingress: AlertIngress, invalid_payload: Any) -> None:
        with pytest.raises(InvalidAlertPayloadError):
            ingress.parse(invalid_payload)

    @pytest.mark.parametrize("payload", [None, "texto", 42, ["lista"]])
    def test_non_object_payload_raises(self, ingress: AlertIngress, payload: Any) -> None:
        with pytest.raises(InvalidAlertPayloadError):
            ingress.parse(payload)
