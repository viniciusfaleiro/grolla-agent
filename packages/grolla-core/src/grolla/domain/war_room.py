"""Ações humanas no war room e a máquina de estados do incidente: decisões puras."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from grolla.domain.incidents import (
    OPEN_STATES,
    Decision,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
    decision_with_cards,
    duration_text,
)


@dataclass(frozen=True)
class AssumeCommander:
    pass


@dataclass(frozen=True)
class ChangeSeverity:
    severity: Severity


@dataclass(frozen=True)
class RecordAction:
    text: str


@dataclass(frozen=True)
class DiscardHypothesis:
    title: str
    reason: str


@dataclass(frozen=True)
class MarkMitigated:
    pass


@dataclass(frozen=True)
class Reopen:
    pass


@dataclass(frozen=True)
class Resolve:
    reason: str


@dataclass(frozen=True)
class DiscardIncident:
    reason: str


HumanAction = (
    AssumeCommander
    | ChangeSeverity
    | RecordAction
    | DiscardHypothesis
    | MarkMitigated
    | Reopen
    | Resolve
    | DiscardIncident
)


class InvalidActionError(ValueError):
    """Ação recusada; a mensagem, em português, é mostrada ao humano."""


CLOSED = "o incidente já está encerrado"
NOT_HANDLING = "só um incidente em tratamento pode ser marcado mitigado"
NOT_MITIGATED = "só um incidente mitigado pode ser reaberto"


def decide_action(
    incident: Incident,
    action: HumanAction,
    author: str,
    now: datetime,
    page_url: Callable[[UUID], str],
) -> Decision:
    """Incidente atualizado, eventos com o autor e o card, se houver; levanta InvalidActionError."""
    author = _required(author, "informe o seu nome antes de agir")
    if incident.state not in OPEN_STATES:
        raise InvalidActionError(CLOSED)
    room = _WarRoom(incident, author, now, page_url)
    match action:
        case AssumeCommander():
            decision = room.assume()
        case ChangeSeverity(severity):
            decision = room.change_severity(severity)
        case RecordAction(text):
            decision = room.record(
                EventType.ACTION_RECORDED, {"text": _required(text, "descreva a ação feita")}
            )
        case DiscardHypothesis(title, reason):
            data = {
                "title": _required(title, "informe a hipótese descartada"),
                "reason": _required(reason, "o motivo do descarte é obrigatório"),
            }
            decision = room.record(EventType.HYPOTHESIS_DISCARDED, data)
        case MarkMitigated():
            if incident.state is not IncidentState.HANDLING:
                raise InvalidActionError(NOT_HANDLING)
            mitigated = replace(incident, state=IncidentState.MITIGATED, mitigated_at=now)
            card = (NotificationKind.INCIDENT_MITIGATED, f"Mitigado por {author}", "")
            decision = room.transition(mitigated, EventType.INCIDENT_MITIGATED, {}, card)
        case Reopen():
            if incident.state is not IncidentState.MITIGATED:
                raise InvalidActionError(NOT_MITIGATED)
            reopened = replace(incident, state=IncidentState.HANDLING)
            decision = room.transition(reopened, EventType.INCIDENT_REOPENED, {}, None)
        case Resolve(reason):
            decision = room.resolve(_required(reason, "o motivo do encerramento é obrigatório"))
        case DiscardIncident(reason):
            reason = _required(reason, "o motivo do descarte é obrigatório")
            discarded = replace(incident, state=IncidentState.DISCARDED, resolved_at=now)
            card = (
                NotificationKind.INCIDENT_DISCARDED,
                f"Descartado por {author}",
                f"Motivo: {reason}",
            )
            decision = room.transition(
                discarded, EventType.INCIDENT_DISCARDED, {"reason": reason}, card
            )
    return decision


def _required(text: str, message: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise InvalidActionError(message)
    return stripped


Card = tuple[NotificationKind, str, str]  # tipo, resumo e evidência


@dataclass(frozen=True)
class _WarRoom:
    """A ação de um autor num incidente aberto, num instante."""

    incident: Incident
    author: str
    now: datetime
    page_url: Callable[[UUID], str]

    def assume(self) -> Decision:
        """Detectado ou Informado passa a Em tratamento; depois disso, só troca o IC."""
        if self.incident.commander == self.author:
            raise InvalidActionError(f"{self.author} já é o IC")
        waiting = self.incident.state in (IncidentState.DETECTED, IncidentState.INFORMED)
        updated = replace(
            self.incident,
            commander=self.author,
            state=IncidentState.HANDLING if waiting else self.incident.state,
            handling_at=self.incident.handling_at or self.now,
        )
        data = {"commander": self.author, "previous": self.incident.commander}
        card = (NotificationKind.COMMANDER_ASSIGNED, f"{self.author} assumiu como IC", "")
        return self.transition(updated, EventType.COMMANDER_ASSIGNED, data, card)

    def change_severity(self, severity: Severity) -> Decision:
        current = self.incident.severity
        if severity == current:
            raise InvalidActionError(f"o incidente já é {severity.value}")
        updated = replace(self.incident, severity=severity)
        data = {"from": current.value, "to": severity.value}
        summary = f"{current.value} → {severity.value} por {self.author}"
        card = (NotificationKind.SEVERITY_CHANGED_BY_HUMAN, summary, "")
        return self._decision(updated, EventType.SEVERITY_CHANGED, data, card)

    def record(self, kind: EventType, data: Mapping[str, Any]) -> Decision:
        """Ação que só entra na timeline: o incidente não muda e não há card."""
        return self._decision(self.incident, kind, data, None)

    def resolve(self, reason: str) -> Decision:
        resolved = replace(self.incident, state=IncidentState.RESOLVED, resolved_at=self.now)
        duration = duration_text(self.now - self.incident.detected_at)
        summary = f"Encerrado por {self.author} · durou {duration}"
        card = (NotificationKind.INCIDENT_RESOLVED, summary, f"Motivo: {reason}")
        return self.transition(resolved, EventType.INCIDENT_RESOLVED, {"reason": reason}, card)

    def transition(
        self, updated: Incident, kind: EventType, data: Mapping[str, Any], card: Card | None
    ) -> Decision:
        """Como `_decision`, com o estado anterior e o novo nos dados do evento."""
        states = {"from": self.incident.state.value, "to": updated.state.value}
        return self._decision(updated, kind, {**data, **states}, card)

    def _decision(
        self, updated: Incident, kind: EventType, data: Mapping[str, Any], card: Card | None
    ) -> Decision:
        event = IncidentEvent(updated.id, self.now, kind, data, author=self.author)
        if card is None:
            return Decision(updated, (event,), ())
        card_kind, summary, evidence = card
        notification = Notification(
            kind=card_kind,
            incident_id=updated.id,
            title=updated.title,
            severity=updated.severity,
            services=updated.services,
            summary=summary,
            evidence=evidence,
            source_url=None,
            page_url=self.page_url(updated.id),
        )
        return decision_with_cards(updated, [event], (notification,), self.now)
