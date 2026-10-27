"""Triagem de incidente novo: sugestão de junção com outro incidente aberto e severidade
sugerida quando nenhuma regra casou. O agente sugere; juntar é humano, e a severidade só sobe.

Contrato de menos ruído: os corpos são do grolla-dominio.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grolla.domain.incidents import Incident, IncidentEvent, Notification, Severity

AGENT_SEVERITY_REASON = "sugerida pelo agente"  # "sugerida pelo agente: <motivo>"
MERGED_REASON = "juntado em"  # motivo do descarte: "juntado em <título do outro>"


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError
