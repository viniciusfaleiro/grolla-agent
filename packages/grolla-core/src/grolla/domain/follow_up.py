"""Acompanhamento da crise: ciclos periódicos em P0/P1, o card só quando muda, as propostas de
mitigar e encerrar e o impacto financeiro estimado.

O modelo de linguagem só devolve dados (tendência, taxa de erro, resumo); quem decide postar,
propor e calcular o impacto é este módulo, e o estado do incidente só muda por ação humana.

Contrato do agente que acompanha a crise: os corpos são do grolla-dominio.
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from grolla.domain.analysis import Evaluation, Evidence, PartialReason
from grolla.domain.incidents import (
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    Severity,
)

ALERT_REOPENED_REASON = "alerta voltou a disparar"
LIMIT_REACHED = "acompanhamento parou: limite de ciclos do incidente"
BUDGET_REACHED = "acompanhamento parou: limite de tokens do incidente"


class Trend(StrEnum):
    WORSE = "piorando"
    STABLE = "estavel"
    BETTER = "melhorando"
    NORMAL = "normalizado"


class Proposal(StrEnum):
    MITIGATE = "mitigar"
    RESOLVE = "encerrar"


@dataclass(frozen=True)
class CycleDraft:
    """O que o modelo devolve num ciclo, já validado."""

    trend: Trend
    error_rate: float | None  # % de erro atual (0 a 100), quando medida
    summary: str
    changed: bool
    evidence: tuple[Evidence, ...] = ()  # até 3


class CycleStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    COMPLETED = "concluido"
    PARTIAL = "parcial"


@dataclass(frozen=True)
class Cycle:
    incident_id: UUID
    number: int  # 1, 2, ...
    status: CycleStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    trend: Trend | None = None
    error_rate: float | None = None
    summary: str = ""
    changed: bool = False
    evidence: tuple[Evidence, ...] = ()
    evaluations: tuple[Evaluation, ...] = ()
    partial_reason: PartialReason | None = None
    posted: bool = False  # o card de atualização foi enviado


@dataclass(frozen=True)
class FollowUpPolicy:
    severities: frozenset[Severity] = frozenset({Severity.P0, Severity.P1})
    cycle: timedelta = timedelta(minutes=5)
    quiet: timedelta = timedelta(minutes=15)  # card curto sem mudança
    stable: timedelta = timedelta(minutes=15)  # normalizado por tanto tempo → proposta
    max_cycles: int = 24
    early: timedelta = timedelta(minutes=1)  # antecipação só se o último começou antes disso


@dataclass(frozen=True)
class CycleDecision:
    cycle: Cycle  # com `posted` decidido
    events: tuple[IncidentEvent, ...]
    notifications: tuple[Notification, ...]


@dataclass(frozen=True)
class MadeProposal:
    proposal: Proposal
    state: IncidentState  # estado do incidente quando a proposta foi feita


def cycle_due(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    incident: Incident,
    *,
    analysis_done_at: datetime | None,
    cycles: Sequence[Cycle],
    requested_at: datetime | None,
    now: datetime,
    policy: FollowUpPolicy,
) -> bool:
    """Se o incidente precisa de um ciclo agora: severidade acompanhada, aberto, análise
    terminada, nenhum ciclo pendente ou em andamento, abaixo do teto, e o intervalo vencido (ou
    pedido antecipado, se o último ciclo começou há mais de `early`).
    """
    raise NotImplementedError


def completed(
    job: Cycle, draft: CycleDraft, evaluations: Sequence[Evaluation], now: datetime
) -> Cycle:
    """Ciclo concluído; link de evidência só fica se veio de uma avaliação."""
    raise NotImplementedError


def partial_cycle(
    job: Cycle, reason: PartialReason, evaluations: Sequence[Evaluation], now: datetime
) -> Cycle:
    raise NotImplementedError


def decide_cycle(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    incident: Incident,
    *,
    cycle: Cycle,
    history: Sequence[Cycle],
    proposals: Collection[MadeProposal],
    impact: str | None,
    now: datetime,
    policy: FollowUpPolicy,
    page_url: Callable[[UUID], str],
) -> CycleDecision:
    """O ciclo terminado vira evento (CYCLE_COMPLETED ou CYCLE_PARTIAL); o card
    FOLLOW_UP_UPDATE sai quando a tendência muda em relação ao último card, quando o modelo diz
    que mudou, ou quando passou `quiet` do último card; a proposta (AGENT_PROPOSAL, evento e
    card) sai quando os ciclos estão `normalizado` sem interrupção há `stable`, uma vez por
    estado; no último ciclo permitido, FOLLOW_UP_STOPPED.
    """
    raise NotImplementedError


def estimated_impact(cycles: Sequence[Cycle], detected_at: datetime, per_minute: float) -> float:
    """Soma, por ciclo, de per_minute vezes a taxa de erro vezes os minutos desde o ciclo
    anterior (o primeiro conta desde a detecção); ciclo sem taxa de erro não soma.
    """
    raise NotImplementedError
