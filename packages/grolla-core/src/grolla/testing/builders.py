"""Construtores de modelos de incidente para testes: valores válidos e únicos por padrão."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from grolla.domain.analysis import (
    Analysis,
    AnalysisDraft,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
)
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


def make_draft(
    *, summary: str = "Deploy 2.4.1 elevou a taxa de erro", hypotheses: int = 2
) -> AnalysisDraft:
    """Rascunho do modelo com `hypotheses` hipóteses, a de maior confiança primeiro."""
    return AnalysisDraft(
        summary=summary,
        hypotheses=tuple(
            Hypothesis(
                title=f"Hipótese {n + 1}",
                confidence=round(0.8 - 0.3 * n, 2),
                evidence=(
                    Evidence(f"Evidência da hipótese {n + 1}", "https://origem.interno/1"),
                    Evidence("Sem link", None),
                ),
            )
            for n in range(hypotheses)
        ),
        plan=("Avaliar reverter o deploy 2.4.1", "Acompanhar a taxa de erro"),
    )


def make_evaluations() -> tuple[Evaluation, ...]:
    """Uma avaliação que deu certo e uma que não foi possível consultar."""
    return (
        Evaluation(
            "query_metric",
            "taxa_de_erro de capture-api, últimos 60 min",
            "Subiu de 0,2% para 7% às 10:02",
            "https://origem.interno/metrica",
            ok=True,
        ),
        Evaluation(
            "search_logs",
            "logs de erro de capture-api, últimos 60 min",
            "não foi possível consultar",
            None,
            ok=False,
        ),
    )


def make_analysis(  # noqa: PLR0913 — cada campo da análise é ajustável no teste
    incident_id: UUID,
    *,
    status: AnalysisStatus = AnalysisStatus.COMPLETED,
    requested_at: datetime = T0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    partial_reason: PartialReason | None = None,
) -> Analysis:
    """Análise no estado pedido: pendente e em andamento vêm vazias; concluída vem cheia."""
    empty = status in (AnalysisStatus.PENDING, AnalysisStatus.RUNNING)
    if status is AnalysisStatus.PARTIAL and partial_reason is None:
        partial_reason = PartialReason.TIMEOUT
    if status is not AnalysisStatus.PENDING and started_at is None:
        started_at = requested_at + timedelta(seconds=5)
    if not empty and finished_at is None:
        finished_at = requested_at + timedelta(minutes=2)
    draft = make_draft()
    full = status is AnalysisStatus.COMPLETED
    return Analysis(
        incident_id=incident_id,
        status=status,
        requested_at=requested_at,
        started_at=started_at,
        finished_at=finished_at,
        summary=draft.summary
        if full
        else ("Análise parcial: tempo esgotado." if not empty else ""),
        evaluations=() if empty else make_evaluations(),
        hypotheses=draft.hypotheses if full else (),
        plan=draft.plan if full else (),
        partial_reason=partial_reason,
    )
