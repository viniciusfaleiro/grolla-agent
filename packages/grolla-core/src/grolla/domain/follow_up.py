"""Acompanhamento da crise: ciclos periódicos em P0/P1, o card só quando muda, as propostas de
mitigar e encerrar e o impacto financeiro estimado.

O modelo de linguagem só devolve dados (tendência, taxa de erro, resumo); quem decide postar,
propor e calcular o impacto é este módulo, e o estado do incidente só muda por ação humana (ou
pela regra determinística do alerta que volta a disparar, em `incidents.decide_firing`).
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from grolla.domain.analysis import PARTIAL_REASON_TEXT, Evaluation, Evidence, PartialReason
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.incidents import (
    OPEN_STATES,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
    duration_text,
)

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


MAX_EVIDENCE = 3
MITIGABLE = frozenset({IncidentState.DETECTED, IncidentState.INFORMED, IncidentState.HANDLING})
ACTIVE = frozenset({CycleStatus.PENDING, CycleStatus.RUNNING})

# Como a tendência aparece para o time.
TREND_TEXT = {
    Trend.WORSE: "piorando",
    Trend.STABLE: "estável",
    Trend.BETTER: "melhorando",
    Trend.NORMAL: "normalizado",
}

# A frase do card e do aviso no war room.
PROPOSAL_TEXT = {
    Proposal.MITIGATE: "Grolla sugere marcar como mitigado",
    Proposal.RESOLVE: "Grolla sugere encerrar",
}


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

    O primeiro ciclo vence `cycle` depois do fim da análise inicial.
    """
    if (
        incident.severity not in policy.severities
        or incident.state not in OPEN_STATES
        or analysis_done_at is None
        or len(cycles) >= policy.max_cycles
        or any(c.status in ACTIVE for c in cycles)
    ):
        return False
    last = cycles[-1] if cycles else None
    since = analysis_done_at if last is None else (last.started_at or last.requested_at)
    if requested_at is not None and now - since >= policy.early:
        return True
    return now - since >= policy.cycle


def completed(
    job: Cycle, draft: CycleDraft, evaluations: Sequence[Evaluation], now: datetime
) -> Cycle:
    """Ciclo concluído; link de evidência só fica se veio de uma avaliação (link que nenhuma
    ferramenta devolveu pode ter sido inventado). Fica com até MAX_EVIDENCE evidências.
    """
    known = {e.url for e in evaluations if e.url}
    evidence = tuple(
        replace(e, url=e.url if e.url in known else None) for e in draft.evidence[:MAX_EVIDENCE]
    )
    return replace(
        job,
        status=CycleStatus.COMPLETED,
        finished_at=now,
        trend=draft.trend,
        error_rate=draft.error_rate,
        summary=draft.summary,
        changed=draft.changed,
        evidence=evidence,
        evaluations=tuple(evaluations),
        partial_reason=None,
    )


def partial_cycle(
    job: Cycle, reason: PartialReason, evaluations: Sequence[Evaluation], now: datetime
) -> Cycle:
    """Ciclo encerrado sem resultado válido: mantém o que foi avaliado e o motivo."""
    return replace(
        job,
        status=CycleStatus.PARTIAL,
        finished_at=now,
        summary=f"Ciclo parcial: {PARTIAL_REASON_TEXT[reason]}.",
        evaluations=tuple(evaluations),
        partial_reason=reason,
    )


def error_rate_text(rate: float | None) -> str:
    """ "12% de erro", "0,5% de erro" ou "erro não medido"."""
    if rate is None:
        return "erro não medido"
    number = f"{rate:.1f}".removesuffix(".0").replace(".", ",")
    return f"{number}% de erro"


def no_change_text(cycle: Cycle) -> str:
    """ "sem mudança: estável, 12% de erro"."""
    trend = "sem tendência" if cycle.trend is None else TREND_TEXT[cycle.trend]
    return f"sem mudança: {trend}, {error_rate_text(cycle.error_rate)}"


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

    `history` são os ciclos anteriores a este. O card completo leva o ciclo em `cycle`; o curto
    ("sem mudança") vai com `cycle=None` e o texto pronto em `summary`; os dois marcam o ciclo
    como `posted`. Ciclo parcial não posta nem propõe, e não interrompe nem conta na sequência
    de `normalizado`.
    """
    earlier = [c for c in history if c.number < cycle.number]
    events: list[IncidentEvent] = []
    cards: list[Notification] = []
    if cycle.status is CycleStatus.COMPLETED:
        card = _update_card(
            incident, cycle, earlier, impact=impact, now=now, policy=policy, page_url=page_url
        )
        cycle = replace(cycle, posted=card is not None)
        events.append(_event(incident, now, EventType.CYCLE_COMPLETED, _completed_data(cycle)))
        if card is not None:
            cards.append(card)
        proposal = _proposal(incident, cycle, earlier, proposals=proposals, now=now, policy=policy)
        if proposal is not None:
            made, reason = proposal
            data = {"proposal": made.value, "state": incident.state.value, "reason": reason}
            events.append(_event(incident, now, EventType.AGENT_PROPOSAL, data))
            text = PROPOSAL_TEXT[made]
            cards.append(_card(NotificationKind.AGENT_PROPOSAL, incident, text, reason, page_url))
    else:
        reason = cycle.partial_reason
        data = {
            "number": cycle.number,
            "reason": None if reason is None else reason.value,
            "summary": cycle.summary,
            "evaluations": len(cycle.evaluations),
        }
        events.append(_event(incident, now, EventType.CYCLE_PARTIAL, data))
    if cycle.number >= policy.max_cycles:
        stopped = {"reason": LIMIT_REACHED, "cycles": cycle.number}
        events.append(IncidentEvent(incident.id, now, EventType.FOLLOW_UP_STOPPED, stopped))
    events += [
        IncidentEvent(incident.id, now, EventType.NOTIFICATION_QUEUED, {"kind": c.kind.value})
        for c in cards
    ]
    return CycleDecision(cycle, tuple(events), tuple(cards))


def _completed_data(cycle: Cycle) -> dict[str, Any]:
    return {
        "number": cycle.number,
        "trend": None if cycle.trend is None else cycle.trend.value,
        "error_rate": cycle.error_rate,
        "summary": cycle.summary,
        "changed": cycle.changed,
        "posted": cycle.posted,
        "evaluations": len(cycle.evaluations),
    }


def _update_card(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    incident: Incident,
    cycle: Cycle,
    earlier: Sequence[Cycle],
    *,
    impact: str | None,
    now: datetime,
    policy: FollowUpPolicy,
    page_url: Callable[[UUID], str],
) -> Notification | None:
    """O card "Atualização" (tendência mudou, ou o modelo diz que mudou), o curto "sem
    mudança" (passou `quiet` do último card) ou nenhum.
    """
    last = next((c for c in reversed(earlier) if c.posted), None)
    rate = error_rate_text(cycle.error_rate)
    if last is None or cycle.changed or cycle.trend != last.trend:
        first = cycle.evidence[0] if cycle.evidence else None
        card = _card(
            NotificationKind.FOLLOW_UP_UPDATE,
            incident,
            cycle.summary,
            rate if first is None else first.text,
            page_url,
            source_url=None if first is None else first.url,
        )
        return replace(card, cycle=replace(cycle, posted=True), impact=impact)
    if now - (last.finished_at or last.requested_at) >= policy.quiet:
        text = no_change_text(cycle)
        card = _card(NotificationKind.FOLLOW_UP_UPDATE, incident, text, rate, page_url)
        return replace(card, impact=impact)
    return None


def _proposal(  # noqa: PLR0913 — decisão pura: tudo entra por parâmetro
    incident: Incident,
    cycle: Cycle,
    earlier: Sequence[Cycle],
    *,
    proposals: Collection[MadeProposal],
    now: datetime,
    policy: FollowUpPolicy,
) -> tuple[Proposal, str] | None:
    """Mitigar (detectado, informado ou em tratamento) ou encerrar (mitigado), com o motivo."""
    if incident.state in MITIGABLE:
        proposal = Proposal.MITIGATE
    elif incident.state is IncidentState.MITIGATED:
        proposal = Proposal.RESOLVE
    else:
        return None
    if MadeProposal(proposal, incident.state) in proposals:
        return None
    since = _normal_since([*earlier, cycle])
    if since is None or now - since < policy.stable:
        return None
    rate = error_rate_text(cycle.error_rate)
    return proposal, f"serviço normalizado há {duration_text(now - since)}, {rate}"


def _normal_since(cycles: Sequence[Cycle]) -> datetime | None:
    """Quando o primeiro ciclo da sequência final de `normalizado` terminou; None sem ela."""
    since: datetime | None = None
    for cycle in reversed(cycles):
        if cycle.status is not CycleStatus.COMPLETED:
            continue  # parcial não diz a tendência: nem interrompe, nem conta
        if cycle.trend is not Trend.NORMAL:
            break
        since = cycle.finished_at or cycle.requested_at
    return since


def _event(
    incident: Incident, at: datetime, kind: EventType, data: Mapping[str, Any]
) -> IncidentEvent:
    return IncidentEvent(incident.id, at, kind, data, AGENT_AUTHOR)


def _card(  # noqa: PLR0913 — o card reúne o incidente, os textos e os links
    kind: NotificationKind,
    incident: Incident,
    summary: str,
    evidence: str,
    page_url: Callable[[UUID], str],
    *,
    source_url: str | None = None,
) -> Notification:
    return Notification(
        kind=kind,
        incident_id=incident.id,
        title=incident.title,
        severity=incident.severity,
        services=incident.services,
        summary=summary,
        evidence=evidence,
        source_url=source_url,
        page_url=page_url(incident.id),
    )


def estimated_impact(cycles: Sequence[Cycle], detected_at: datetime, per_minute: float) -> float:
    """Soma, por ciclo, de per_minute vezes a taxa de erro vezes os minutos desde o ciclo
    anterior (o primeiro conta desde a detecção); ciclo sem taxa de erro não soma, e o próximo
    medido cobre o intervalo dele.
    """
    total = 0.0
    previous = detected_at
    for cycle in sorted(cycles, key=lambda c: c.number):
        if cycle.status is not CycleStatus.COMPLETED or cycle.error_rate is None:
            continue
        at = cycle.finished_at or cycle.requested_at
        minutes = max(0.0, (at - previous).total_seconds() / 60)
        total += per_minute * cycle.error_rate / 100 * minutes
        previous = at
    return total
