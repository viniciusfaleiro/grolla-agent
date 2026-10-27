"""Triagem de incidente novo: sugestão de junção com outro incidente aberto e severidade
sugerida quando nenhuma regra casou. O agente sugere; juntar é humano, e a severidade só sobe.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from grolla.domain.incidents import (
    OPEN_STATES,
    SYSTEM_AUTHOR,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
    decision_with_cards,
    state_for,
    worst,
)
from grolla.domain.war_room import InvalidActionError

AGENT_SEVERITY_REASON = "sugerida pelo agente"  # "sugerida pelo agente: <motivo>"
MERGED_REASON = "juntado em"  # motivo do descarte: "juntado em <título do outro>"
SAME_PROBLEM = "Parece o mesmo problema que"
NO_AUTHOR = "informe o seu nome antes de agir"
SAME_INCIDENT = "não dá para juntar um incidente nele mesmo"
NOT_OPEN = "só dá para juntar incidentes abertos"


@dataclass(frozen=True)
class TriageDraft:
    """O que o modelo devolve na triagem, já validado."""

    same_as: UUID | None  # outro incidente aberto que é o mesmo problema
    same_reason: str
    severity: Severity | None  # só quando a severidade veio do padrão
    severity_reason: str


@dataclass(frozen=True)
class TriageDecision:
    incident: Incident | None  # com a severidade elevada; None se nada muda no incidente
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


@dataclass(frozen=True)
class MergeDecision:
    source: Incident  # o juntado, descartado
    target: Incident  # o principal, com serviços, alertas disparando e a pior severidade
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


def needs_model(incident: Incident, others: Sequence[Incident], opened_by_default: bool) -> bool:
    """Só chama o modelo com outro incidente aberto de outro serviço ou sem regra de severidade."""
    services = set(incident.services)
    return opened_by_default or any(set(o.services) - services for o in others)


def decide_triage(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    incident: Incident,
    *,
    others: Sequence[Incident],
    draft: TriageDraft | None,
    opened_by_default: bool,
    now: datetime,
    page_url: Callable[[UUID], str],
) -> TriageDecision:
    """TRIAGE_COMPLETED sempre; MERGE_SUGGESTED (evento e card) quando `same_as` é um dos
    `others`; SEVERITY_CHANGED com "sugerida pelo agente: <motivo>" e card SEVERITY_RAISED quando
    a sugestão é mais alta e a severidade veio do padrão.
    """
    updated = incident
    events: list[IncidentEvent] = []
    cards: list[Notification] = []
    suggested = draft.severity if draft is not None and opened_by_default else None
    if suggested is not None and worst(suggested, incident.severity) == incident.severity:
        suggested = None  # a sugestão só sobe a severidade
    if draft is not None and suggested is not None:
        reason = f"{AGENT_SEVERITY_REASON}: {draft.severity_reason}"
        updated = _raised(incident, suggested, now, events, reason=reason)
        summary = f"{incident.severity.value} → {suggested.value} {AGENT_SEVERITY_REASON}"
        evidence = draft.severity_reason
        cards.append(_card(NotificationKind.SEVERITY_RAISED, updated, summary, evidence, page_url))
    other = next((o for o in others if draft is not None and o.id == draft.same_as), None)
    if draft is not None and other is not None:
        data = {"other": str(other.id), "reason": draft.same_reason}
        events.append(IncidentEvent(incident.id, now, EventType.MERGE_SUGGESTED, data))
        summary = f"{SAME_PROBLEM} {other.title}"
        cards.append(
            _card(NotificationKind.MERGE_SUGGESTED, updated, summary, draft.same_reason, page_url)
        )
    decided = {
        "same_as": None if other is None else str(other.id),
        "severity": None if suggested is None else suggested.value,
    }
    events.append(IncidentEvent(incident.id, now, EventType.TRIAGE_COMPLETED, decided))
    decision = decision_with_cards(updated, events, tuple(cards), now)
    changed = updated if updated != incident else None
    return TriageDecision(changed, decision.events, decision.notifications)


def decide_merge(
    source: Incident,
    target: Incident,
    *,
    author: str,
    now: datetime,
    page_url: Callable[[UUID], str],
) -> MergeDecision:
    """Juntar `source` em `target`; levanta InvalidActionError para o mesmo incidente, sem
    autor ou com algum dos dois fora de `OPEN_STATES`.
    """
    author = author.strip()
    if not author:
        raise InvalidActionError(NO_AUTHOR)
    if source.id == target.id:
        raise InvalidActionError(SAME_INCIDENT)
    if source.state not in OPEN_STATES or target.state not in OPEN_STATES:
        raise InvalidActionError(NOT_OPEN)

    # O juntado sai dos disparando: alerta novo daquele fingerprint cai no principal.
    discarded = replace(source, state=IncidentState.DISCARDED, resolved_at=now, firing=frozenset())
    reason = f"{MERGED_REASON} {target.title}"
    states = {"from": source.state.value, "to": discarded.state.value}
    events = [
        IncidentEvent(
            source.id, now, EventType.INCIDENT_DISCARDED, {"reason": reason, **states}, author
        ),
        IncidentEvent(source.id, now, EventType.MERGED_INTO, {"into": str(target.id)}, author),
    ]

    merged = replace(
        target,
        services=tuple(dict.fromkeys((*target.services, *source.services))),
        firing=target.firing | source.firing,
        last_alert_at=max(target.last_alert_at, source.last_alert_at),
    )
    target_events: list[IncidentEvent] = []
    severity = worst(target.severity, source.severity)
    if severity != target.severity:
        merged = _raised(merged, severity, now, target_events, author=author)
    target_events.append(
        IncidentEvent(target.id, now, EventType.INCIDENT_MERGED, {"merged": str(source.id)}, author)
    )
    summary = f"{author} juntou {source.title} neste incidente"
    card = _card(NotificationKind.INCIDENT_MERGED, merged, summary, "", page_url)
    decision = decision_with_cards(merged, target_events, (card,), now)
    return MergeDecision(discarded, merged, (*events, *decision.events), decision.notifications)


def _raised(  # noqa: PLR0913 — os dados da mudança entram por parâmetro
    incident: Incident,
    severity: Severity,
    now: datetime,
    events: list[IncidentEvent],
    *,
    reason: str | None = None,
    author: str = SYSTEM_AUTHOR,
) -> Incident:
    """A severidade sobe e, como no alerta, Detectado e Informado seguem a severidade."""
    data: dict[str, Any] = {"from": incident.severity.value, "to": severity.value}
    if reason is not None:
        data["reason"] = reason
    events.append(IncidentEvent(incident.id, now, EventType.SEVERITY_CHANGED, data, author))
    raised = replace(incident, severity=severity)
    if raised.state in (IncidentState.DETECTED, IncidentState.INFORMED):
        target = state_for(severity)
        if target != raised.state:
            change = {"from": raised.state.value, "to": target.value}
            events.append(IncidentEvent(raised.id, now, EventType.STATE_CHANGED, change, author))
            raised = replace(raised, state=target)
    return raised


def _card(
    kind: NotificationKind,
    incident: Incident,
    summary: str,
    evidence: str,
    page_url: Callable[[UUID], str],
) -> Notification:
    return Notification(
        kind=kind,
        incident_id=incident.id,
        title=incident.title,
        severity=incident.severity,
        services=incident.services,
        summary=summary,
        evidence=evidence,
        source_url=None,
        page_url=page_url(incident.id),
    )
