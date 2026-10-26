"""Ações humanas no war room e a máquina de estados do incidente: decisões puras."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grolla.domain.incidents import Decision, Incident, Severity


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


def decide_action(
    incident: Incident,
    action: HumanAction,
    author: str,
    now: datetime,
    page_url: Callable[[UUID], str],
) -> Decision:
    """Incidente atualizado, eventos com o autor e o card, se houver; levanta InvalidActionError."""
    raise NotImplementedError
