"""Incidentes: alertas, severidade, agrupamento e as decisões puras sobre cada alerta recebido."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from grolla.domain.analysis import Analysis, Verification

if TYPE_CHECKING:  # o acompanhamento importa este módulo
    from grolla.domain.follow_up import Cycle

UNKNOWN_SERVICE = "desconhecido"
SYSTEM_AUTHOR = "sistema"  # autor dos eventos que o núcleo grava sozinho
ALERT_REOPENED_REASON = "alerta voltou a disparar"  # mitigado volta para em tratamento


class Severity(StrEnum):
    """P0 é a pior."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


def worst(a: Severity, b: Severity) -> Severity:
    return min(a, b)  # "P0" < "P1" < ...: a menor é a pior


def labels_match(labels: Mapping[str, str], match: Mapping[str, str]) -> bool:
    """Todos os pares de `match` estão em `labels`."""
    return all(labels.get(key) == value for key, value in match.items())


class IncidentState(StrEnum):
    DETECTED = "detectado"
    INFORMED = "informado"
    HANDLING = "em_tratamento"
    MITIGATED = "mitigado"
    RESOLVED = "resolvido"
    DISCARDED = "descartado"


# Resolvido e Descartado são finais: não recebem alertas nem ações.
OPEN_STATES = frozenset(
    {
        IncidentState.DETECTED,
        IncidentState.INFORMED,
        IncidentState.HANDLING,
        IncidentState.MITIGATED,
    }
)


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
    commander: str | None = None  # IC: quem assumiu no war room
    handling_at: datetime | None = None  # TTA
    mitigated_at: datetime | None = None  # TTM
    resolved_at: datetime | None = None  # TTR (encerrado ou descartado)


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
    ANALYSIS_REQUESTED = "analysis_requested"
    ANALYSIS_COMPLETED = "analysis_completed"
    ANALYSIS_PARTIAL = "analysis_partial"
    VERIFICATION_REQUESTED = "verification_requested"
    VERIFICATION_COMPLETED = "verification_completed"
    VERIFICATION_PARTIAL = "verification_partial"
    COMMANDER_ASSIGNED = "commander_assigned"
    ACTION_RECORDED = "action_recorded"
    HYPOTHESIS_DISCARDED = "hypothesis_discarded"
    INCIDENT_MITIGATED = "incident_mitigated"
    INCIDENT_REOPENED = "incident_reopened"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_DISCARDED = "incident_discarded"
    HUMAN_MESSAGE = "human_message"
    AGENT_MESSAGE = "agent_message"
    CYCLE_COMPLETED = "cycle_completed"
    CYCLE_PARTIAL = "cycle_partial"
    FOLLOW_UP_STOPPED = "follow_up_stopped"
    AGENT_PROPOSAL = "agent_proposal"
    SEEN_BEFORE_ANSWERED = "seen_before_answered"  # data: {"past": <id>, "same": bool}
    TRIAGE_COMPLETED = "triage_completed"
    INCIDENT_OPENED_MANUALLY = "incident_opened_manually"  # data: {"origin": "console", ...}
    MERGE_SUGGESTED = "merge_suggested"  # data: {"other": <id>, "reason": str}
    INCIDENT_MERGED = "incident_merged"  # no principal; data: {"merged": <id>}
    MERGED_INTO = "merged_into"  # no juntado; data: {"into": <id>}


@dataclass(frozen=True)
class IncidentEvent:
    incident_id: UUID
    at: datetime
    type: EventType
    data: Mapping[str, Any]
    author: str = SYSTEM_AUTHOR


class NotificationKind(StrEnum):
    INCIDENT_OPENED = "incident_opened"  # card "investigando"
    ANALYSIS_READY = "analysis_ready"
    ROOT_CAUSE = "root_cause"
    SEVERITY_RAISED = "severity_raised"
    INCIDENT_NORMALIZED = "incident_normalized"
    COMMANDER_ASSIGNED = "commander_assigned"
    SEVERITY_CHANGED_BY_HUMAN = "severity_changed_by_human"
    INCIDENT_MITIGATED = "incident_mitigated"
    INCIDENT_RESOLVED = "incident_resolved"
    INCIDENT_DISCARDED = "incident_discarded"
    FOLLOW_UP_UPDATE = "follow_up_update"  # "Atualização" ou "Sem mudança"
    AGENT_PROPOSAL = "agent_proposal"  # "Grolla sugere marcar como mitigado/encerrar"
    ALERT_REOPENED = "alert_reopened"  # mitigado voltou para em tratamento
    MERGE_SUGGESTED = "merge_suggested"  # "Parece o mesmo problema que <outro>"
    INCIDENT_MERGED = "incident_merged"


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
    analysis: Analysis | None = None  # só em ANALYSIS_READY
    verification: Verification | None = None  # só em ROOT_CAUSE
    cycle: "Cycle | None" = None  # só em FOLLOW_UP_UPDATE
    impact: str | None = None  # impacto estimado já formatado ("R$ 3.000,00 estimado")


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
        for index, rule in enumerate(self.rules):
            if labels_match(alert.labels, rule.match):
                return rule.severity, index
        return self.default, None


@dataclass(frozen=True)
class Decision:
    incident: Incident | None  # None: nada a gravar
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]
    request_analysis: bool = False  # True só quando o alerta abre um incidente


GROUPING_REASON = "mesmo serviço"
MINUTES_PER_HOUR = 60
DEFAULT_SEVERITY_REASON = "severidade padrão"
URGENT = frozenset({Severity.P0, Severity.P1})


def state_for(severity: Severity) -> IncidentState:
    """P0/P1 esperam o acionamento em Detectado; P2/P3 já ficam Informado."""
    return IncidentState.DETECTED if severity in URGENT else IncidentState.INFORMED


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
    """Alerta disparando: entra no incidente ativo mais recente do serviço ou abre um novo."""
    severity, rule = policy.classify(alert)
    alert_data = {
        **_alert_data(alert),
        "severity": severity.value,
        "severity_rule": rule,
        "severity_reason": DEFAULT_SEVERITY_REASON if rule is None else f"regra {rule + 1}",
    }
    active = [
        c
        for c in candidates
        if c.state in OPEN_STATES
        and alert.service in c.services
        and (c.firing or c.last_alert_at >= now - window)
    ]
    current = max(active, key=lambda c: c.last_alert_at, default=None)

    if current is None:
        incident = Incident(
            id=new_id(),
            title=alert.rule_name,
            state=IncidentState.DETECTED,
            severity=severity,
            services=(alert.service,),
            detected_at=now,
            last_alert_at=now,
            firing=frozenset({alert.fingerprint}),
        )
        events = [_event(incident, now, EventType.ALERT_RECEIVED, alert_data)]
        kind: NotificationKind | None = NotificationKind.INCIDENT_OPENED
    else:
        raised = worst(current.severity, severity)
        incident = replace(
            current,
            severity=raised,
            services=tuple(dict.fromkeys((*current.services, alert.service))),
            last_alert_at=now,
            firing=current.firing | {alert.fingerprint},
        )
        events = [
            _event(
                incident, now, EventType.ALERT_GROUPED, {**alert_data, "reason": GROUPING_REASON}
            )
        ]
        kind = None
        if raised != current.severity:
            events.append(
                _event(
                    incident,
                    now,
                    EventType.SEVERITY_CHANGED,
                    {"from": current.severity.value, "to": raised.value},
                )
            )
            kind = NotificationKind.SEVERITY_RAISED

    # Só Detectado e Informado seguem a severidade; Em tratamento é do humano. Mitigado volta
    # para Em tratamento quando um alerta volta a disparar, mantendo IC, TTA e TTM.
    waiting = incident.state in (IncidentState.DETECTED, IncidentState.INFORMED)
    reopened = incident.state is IncidentState.MITIGATED
    target = state_for(incident.severity) if waiting else incident.state
    if reopened:
        target = IncidentState.HANDLING
    if target != incident.state:
        change = {"from": incident.state.value, "to": target.value}
        if reopened:
            change["reason"] = ALERT_REOPENED_REASON
        events.append(_event(incident, now, EventType.STATE_CHANGED, change))
        incident = replace(incident, state=target)

    if current is None:
        events.append(_event(incident, now, EventType.ANALYSIS_REQUESTED, {}))
    notifications = () if kind is None else (_notification(kind, incident, alert, page_url),)
    if reopened:
        card = _notification(NotificationKind.ALERT_REOPENED, incident, alert, page_url)
        summary = f"{ALERT_REOPENED_REASON}: {alert.rule_name}; incidente volta a em tratamento"
        notifications = (*notifications, replace(card, summary=summary))
    return replace(
        decision_with_cards(incident, events, notifications, now), request_analysis=current is None
    )


def decide_resolved(
    alert: Alert, incident: Incident, now: datetime, page_url: Callable[[UUID], str]
) -> Decision:
    """Alerta resolvido: sai dos disparando; sem nenhum disparando, o incidente normalizou."""
    updated = replace(incident, last_alert_at=now, firing=incident.firing - {alert.fingerprint})
    events = [_event(updated, now, EventType.ALERT_RESOLVED, _alert_data(alert))]
    notifications: tuple[Notification, ...] = ()
    if incident.state in OPEN_STATES and incident.firing and not updated.firing:
        events.append(_event(updated, now, EventType.INCIDENT_NORMALIZED, {}))
        card = _notification(NotificationKind.INCIDENT_NORMALIZED, updated, alert, page_url)
        summary = f"Normalizado: durou {duration_text(now - incident.detected_at)}"
        notifications = (replace(card, summary=summary),)
    return decision_with_cards(updated, events, notifications, now)


def duration_text(duration: timedelta) -> str:
    """ "32 min" ou "1 h 05 min"; menos de 1 minuto conta como 1."""
    minutes = max(1, round(duration.total_seconds() / 60))
    if minutes < MINUTES_PER_HOUR:
        return f"{minutes} min"
    return f"{minutes // MINUTES_PER_HOUR} h {minutes % MINUTES_PER_HOUR:02d} min"


def _alert_data(alert: Alert) -> dict[str, Any]:
    return {
        "fingerprint": alert.fingerprint,
        "rule_name": alert.rule_name,
        "service": alert.service,
        "summary": alert.summary,
        "starts_at": alert.starts_at.isoformat(),
    }


def _event(
    incident: Incident, at: datetime, kind: EventType, data: Mapping[str, Any]
) -> IncidentEvent:
    return IncidentEvent(incident.id, at, kind, data)


def _notification(
    kind: NotificationKind, incident: Incident, alert: Alert, page_url: Callable[[UUID], str]
) -> Notification:
    return Notification(
        kind=kind,
        incident_id=incident.id,
        title=incident.title,
        severity=incident.severity,
        services=incident.services,
        summary=alert.summary,
        evidence=alert.description,
        source_url=alert.source_url,
        page_url=page_url(incident.id),
    )


def decision_with_cards(
    incident: Incident,
    events: list[IncidentEvent],
    notifications: tuple[Notification, ...],
    now: datetime,
) -> Decision:
    """A decisão com os eventos e, para cada card, o evento `notification_queued`."""
    queued = [
        _event(incident, now, EventType.NOTIFICATION_QUEUED, {"kind": n.kind.value})
        for n in notifications
    ]
    return Decision(incident, (*events, *queued), notifications)


def decide_manual(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    *,
    title: str,
    service: str,
    severity: Severity,
    author: str,
    origin: str,
    now: datetime,
    page_url: Callable[[UUID], str],
    new_id: Callable[[], UUID],
) -> Decision:
    """Incidente aberto por uma pessoa, sem alerta: nasce no estado da severidade, com o evento
    INCIDENT_OPENED_MANUALLY (autor e origem), o card INCIDENT_OPENED e o pedido de análise.
    Levanta ValueError para título, serviço ou autor vazios.

    Contrato do console de operação: o corpo é do grolla-dominio.
    """
    raise NotImplementedError
