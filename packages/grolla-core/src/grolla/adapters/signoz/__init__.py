"""SigNoz: webhook de alertas (formato compatível com Alertmanager) como AlertIngress."""

import base64
import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from grolla.application.incidents import InvalidAlertPayloadError
from grolla.domain.incidents import UNKNOWN_SERVICE, Alert, AlertStatus

_STATUS = {"firing": AlertStatus.FIRING, "resolved": AlertStatus.RESOLVED}


class _Alert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = {}
    starts_at: AwareDatetime = Field(alias="startsAt")
    ends_at: AwareDatetime | None = Field(default=None, alias="endsAt")
    generator_url: str | None = Field(default=None, alias="generatorURL")
    fingerprint: str


class _Payload(BaseModel):
    alerts: list[dict[str, Any]]


class SignozAlertIngress:
    def __init__(self, username: str, password: str, service_labels: Sequence[str]) -> None:
        self._username = username
        self._password = password
        self._service_labels = tuple(service_labels)

    def authenticate(self, headers: Mapping[str, str]) -> bool:
        """HTTP Basic, comparado em tempo constante; header ausente ou malformado é recusa."""
        value = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        scheme, _, encoded = value.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True).decode()
        except ValueError:  # base64 inválido ou bytes fora de UTF-8
            return False
        username, separator, password = decoded.partition(":")
        user_ok = hmac.compare_digest(username.encode(), self._username.encode())
        password_ok = hmac.compare_digest(password.encode(), self._password.encode())
        return bool(separator) and user_ok and password_ok

    def parse(self, payload: Any) -> list[Alert]:
        try:
            raws = _Payload.model_validate(payload).alerts
            return [self._alert(_Alert.model_validate(raw), raw) for raw in raws]
        except ValidationError as error:
            raise InvalidAlertPayloadError(str(error)) from error

    def _alert(self, alert: _Alert, raw: Mapping[str, Any]) -> Alert:
        service = next(
            (alert.labels[label] for label in self._service_labels if label in alert.labels),
            UNKNOWN_SERVICE,
        )
        return Alert(
            fingerprint=alert.fingerprint,
            status=_STATUS[alert.status],
            rule_name=alert.labels.get("alertname", ""),
            service=service,
            labels=alert.labels,
            summary=alert.annotations.get("summary", ""),
            description=alert.annotations.get("description", ""),
            starts_at=alert.starts_at.astimezone(UTC),
            ends_at=_end(alert.ends_at),
            source_url=alert.generator_url or None,
            raw=raw,
        )


def _end(ends_at: datetime | None) -> datetime | None:
    """O Alertmanager manda "0001-01-01T00:00:00Z" para alerta sem fim."""
    if ends_at is None or ends_at.year == 1:
        return None
    return ends_at.astimezone(UTC)
