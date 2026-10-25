"""Construtores de modelos de incidente para testes: valores válidos e únicos por padrão."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from grolla.domain.incidents import (
    Alert,
    AlertStatus,
    Incident,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
)

T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


def make_alert(  # noqa: PLR0913 — cada campo do alerta é ajustável no teste
    *,
    fingerprint: str | None = None,
    status: AlertStatus = AlertStatus.FIRING,
    rule_name: str = "Taxa de erro alta",
    service: str | None = None,
    labels: Mapping[str, str] | None = None,
    starts_at: datetime = T0,
    summary: str = "Taxa de erro de 7%",
    description: str = "A taxa de erro está em 7% nos últimos 5 minutos.",
    source_url: str | None = "https://observabilidade.interno/alerta/1",
) -> Alert:
    """Alerta com fingerprint e serviço únicos quando não informados."""
    service = service or f"servico-{uuid4().hex[:8]}"
    return Alert(
        fingerprint=fingerprint or uuid4().hex,
        status=status,
        rule_name=rule_name,
        service=service,
        labels=dict(labels) if labels is not None else {"service_name": service},
        summary=summary,
        description=description,
        starts_at=starts_at,
        ends_at=None if status is AlertStatus.FIRING else starts_at,
        source_url=source_url,
        raw={"labels": {"service_name": service}, "status": status.value},
    )


def make_incident(  # noqa: PLR0913 — cada campo do incidente é ajustável no teste
    *,
    id: UUID | None = None,  # noqa: A002 — mesmo nome do campo do modelo
    state: IncidentState = IncidentState.INFORMED,
    severity: Severity = Severity.P3,
    services: tuple[str, ...] | None = None,
    detected_at: datetime = T0,
    last_alert_at: datetime | None = None,
    firing: frozenset[str] = frozenset(),
) -> Incident:
    """Incidente com id e serviço únicos quando não informados."""
    return Incident(
        id=id or uuid4(),
        title="Taxa de erro alta",
        state=state,
        severity=severity,
        services=services or (f"servico-{uuid4().hex[:8]}",),
        detected_at=detected_at,
        last_alert_at=last_alert_at or detected_at,
        firing=firing,
    )


def make_notification(
    incident_id: UUID,
    kind: NotificationKind = NotificationKind.INCIDENT_OPENED,
    **overrides: Any,
) -> Notification:
    fields: dict[str, Any] = {
        "kind": kind,
        "incident_id": incident_id,
        "title": "Taxa de erro alta",
        "severity": Severity.P2,
        "services": ("capture-api",),
        "summary": "Taxa de erro de 7%",
        "evidence": "A taxa de erro está em 7% nos últimos 5 minutos.",
        "source_url": "https://observabilidade.interno/alerta/1",
        "page_url": f"https://grolla.interno/incidents/{incident_id}",
    }
    fields.update(overrides)
    return Notification(**fields)
