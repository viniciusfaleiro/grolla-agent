"""Incidentes: alertas, severidade, agrupamento e as decisões puras sobre cada alerta recebido."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

UNKNOWN_SERVICE = "desconhecido"


class Severity(StrEnum):
    """P0 é a pior."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


def worst(a: Severity, b: Severity) -> Severity:
    raise NotImplementedError


class IncidentState(StrEnum):
    DETECTED = "detectado"
    INFORMED = "informado"


class AlertStatus(StrEnum):
    FIRING = "disparando"
    RESOLVED = "resolvido"


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    status: AlertStatus
    rule_name: str
    service: str  # UNKNOWN_SERVICE quando o alerta não tem label de serviço
    labels: Mapping[str, str]
    summary: str
    description: str
    starts_at: datetime
    ends_at: datetime | None
    source_url: str | None
    raw: Mapping[str, Any]  # alerta bruto, para histórico


@dataclass(frozen=True)
class Incident:
    id: UUID
    title: str
    state: IncidentState
    severity: Severity
    services: tuple[str, ...]
    detected_at: datetime
    last_alert_at: datetime
    firing: frozenset[str]  # fingerprints disparando


class EventType(StrEnum):
    ALERT_RECEIVED = "alert_received"
    ALERT_GROUPED = "alert_grouped"
    ALERT_RESOLVED = "alert_resolved"
    SEVERITY_CHANGED = "severity_changed"
    STATE_CHANGED = "state_changed"
    INCIDENT_NORMALIZED = "incident_normalized"
    NOTIFICATION_QUEUED = "notification_queued"
    NOTIFICATION_DELIVERED = "notification_delivered"
    NOTIFICATION_FAILED = "notification_failed"


@dataclass(frozen=True)
class IncidentEvent:
    incident_id: UUID
    at: datetime
    type: EventType
    data: Mapping[str, Any]
    author: str = "sistema"


class NotificationKind(StrEnum):
    INCIDENT_OPENED = "incident_opened"
    SEVERITY_RAISED = "severity_raised"
    INCIDENT_NORMALIZED = "incident_normalized"


@dataclass(frozen=True)
class Notification:
    kind: NotificationKind
    incident_id: UUID
    title: str
    severity: Severity
    services: tuple[str, ...]
    summary: str
    evidence: str  # descrição do alerta que motivou
    source_url: str | None
    page_url: str


@dataclass(frozen=True)
class SeverityRule:
    match: Mapping[str, str]
    severity: Severity


@dataclass(frozen=True)
class SeverityPolicy:
    rules: tuple[SeverityRule, ...]
    default: Severity

    def classify(self, alert: Alert) -> tuple[Severity, int | None]:
        """Severidade da primeira regra que casa e o índice dela; sem regra, o padrão e None."""
        raise NotImplementedError


@dataclass(frozen=True)
class Decision:
    incident: Incident | None  # None: nada a gravar
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


def decide_firing(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    alert: Alert,
    *,
    candidates: Sequence[Incident],
    policy: SeverityPolicy,
    window: timedelta,
    now: datetime,
    page_url: Callable[[UUID], str],
    new_id: Callable[[], UUID],
) -> Decision:
    raise NotImplementedError


def decide_resolved(
    alert: Alert, incident: Incident, now: datetime, page_url: Callable[[UUID], str]
) -> Decision:
    raise NotImplementedError
