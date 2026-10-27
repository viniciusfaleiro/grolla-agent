"""Casos de uso do acompanhamento da crise: agendar, rodar e destravar os ciclos, e o impacto
estimado para a página.
"""

import asyncio
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.analysis import REAP_GRACE, failure_reason, investigation_brief
from grolla.application.chat import chat_context
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
    TokenBudget,
    Toolbox,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder
from grolla.domain.analysis import PartialReason
from grolla.domain.follow_up import (
    BUDGET_REACHED,
    Cycle,
    CycleStatus,
    FollowUpPolicy,
    MadeProposal,
    Proposal,
    completed,
    cycle_due,
    decide_cycle,
    estimated_impact,
    partial_cycle,
)
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    Severity,
)


@dataclass(frozen=True)
class CycleCandidate:
    incident: Incident
    analysis_done_at: datetime | None  # fim da análise inicial; None se ainda não terminou
    cycles: tuple[Cycle, ...]  # em ordem de número
    requested_at: datetime | None  # pedido de ciclo antecipado (alerta agrupado ou mensagem)
    tokens: int  # tokens de acompanhamento já usados no incidente


class CycleQueue(Protocol):
    async def candidates(self, severities: Collection[Severity]) -> list[CycleCandidate]:
        """Incidentes abertos (`OPEN_STATES`) com essas severidades."""
        ...

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        """Cria o ciclo pendente; False se o número já existe (corrida entre workers)."""
        ...

    async def claim(self, now: datetime) -> Cycle | None:
        """O ciclo pendente mais antigo, marcado em andamento."""
        ...

    async def stuck(self, started_before: datetime) -> list[Cycle]: ...

    async def finish(
        self,
        cycle: Cycle,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Numa transação, só se o ciclo ainda está em andamento: grava o ciclo, os eventos, os
        cards na outbox e o uso (etapa follow_up), e zera o pedido antecipado.
        """
        ...


@dataclass(frozen=True)
class ImpactPolicy:
    currency: str = "BRL"
    per_minute: Mapping[str, float] = field(default_factory=dict[str, float])


@dataclass(frozen=True)
class FollowUpLimits:
    timeout: timedelta = timedelta(minutes=3)
    max_tokens: int = 40_000  # por ciclo
    max_tokens_per_incident: int = 600_000


LOOKBACK = timedelta(minutes=30)  # janela das ferramentas no ciclo: o que mudou agora


async def schedule_cycles(
    queue: CycleQueue,
    repository: IncidentRepository,
    clock: Clock,
    policy: FollowUpPolicy,
    limits: FollowUpLimits,
) -> int:
    """Enfileira os ciclos devidos (`cycle_due`), sem passar do teto de tokens do incidente;
    devolve quantos.

    Com o teto atingido, o ciclo devido não é enfileirado e a timeline registra uma vez
    FOLLOW_UP_STOPPED (BUDGET_REACHED).
    """
    now = clock.now()
    enqueued = 0
    for candidate in await queue.candidates(policy.severities):
        incident = candidate.incident
        due = cycle_due(
            incident,
            analysis_done_at=candidate.analysis_done_at,
            cycles=candidate.cycles,
            requested_at=candidate.requested_at,
            now=now,
            policy=policy,
        )
        if not due:
            continue
        if candidate.tokens >= limits.max_tokens_per_incident:
            await _stop_for_budget(repository, incident, candidate.tokens, now)
            continue
        number = max((c.number for c in candidate.cycles), default=0) + 1
        if await queue.enqueue(incident.id, number, now):
            enqueued += 1
    return enqueued


async def _stop_for_budget(
    repository: IncidentRepository, incident: Incident, tokens: int, now: datetime
) -> None:
    events = await repository.events_of(incident.id)
    if any(e.type is EventType.FOLLOW_UP_STOPPED for e in events):
        return
    data = {"reason": BUDGET_REACHED, "tokens": tokens}
    async with repository.transaction() as uow:
        await uow.append([IncidentEvent(incident.id, now, EventType.FOLLOW_UP_STOPPED, data)])


async def run_next_cycle(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: CycleQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    cloud: CloudInventory,
    clock: Clock,
    policy: FollowUpPolicy,
    limits: FollowUpLimits,
    impact: ImpactPolicy,
    page_url: Callable[[UUID], str],
    memory: MemoryStore | None = None,
    embedder: TextEmbedder | None = None,
) -> bool:
    """Roda o próximo ciclo; False se não havia. Falha vira ciclo parcial; o incidente segue.

    O modelo recebe o contexto do chat (estado, análise, validação, timeline, chat e memória) e
    o último ciclo concluído, com as ferramentas da investigação.
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    incident = await repository.get_incident(job.incident_id)
    if incident is None:  # o ciclo referencia o incidente: só acontece com dado corrompido
        raise LookupError(f"incidente {job.incident_id} do ciclo não existe")
    history = [c for c in await repository.cycles_of(incident.id) if c.number < job.number]
    previous = next((c for c in reversed(history) if c.status is CycleStatus.COMPLETED), None)
    brief = await investigation_brief(repository, memory, incident, clock, embedder)
    events = await repository.events_of(incident.id)
    context = chat_context(
        incident,
        await repository.analysis_of(incident.id),
        await repository.verification_of(incident.id),
        events,
        await repository.chat_of(incident.id),
        memory=brief.memory,
    )
    toolbox = Toolbox(telemetry, changes, clock, LOOKBACK, cloud, memory=memory, embedder=embedder)
    budget = TokenBudget(limits.max_tokens)
    deadline = asyncio.timeout(limits.timeout.total_seconds())
    try:
        async with deadline:
            draft = await model.follow_up(brief, context, previous, toolbox, budget)
    except Exception as error:  # qualquer falha vira ciclo parcial: o incidente segue
        reason = failure_reason(error, timed_out=deadline.expired())
        cycle = partial_cycle(job, reason, toolbox.evaluations, clock.now())
    else:
        cycle = completed(job, draft, toolbox.evaluations, clock.now())
    now = clock.now()
    decision = decide_cycle(
        incident,
        cycle=cycle,
        history=history,
        proposals=made_proposals(events),
        impact=_impact(incident, [*history, cycle], impact),
        now=now,
        policy=policy,
        page_url=page_url,
    )
    await queue.finish(decision.cycle, decision.events, decision.notifications, budget.usages, now)
    return True


def made_proposals(events: Sequence[IncidentEvent]) -> list[MadeProposal]:
    """As propostas já feitas, tiradas dos eventos AGENT_PROPOSAL."""
    return [
        MadeProposal(Proposal(e.data["proposal"]), IncidentState(e.data["state"]))
        for e in events
        if e.type is EventType.AGENT_PROPOSAL
    ]


async def reap_stuck_cycles(
    queue: CycleQueue,
    repository: IncidentRepository,
    clock: Clock,
    policy: FollowUpPolicy,
    limits: FollowUpLimits,
) -> int:
    """Encerra como parciais (interrompidos) os ciclos presos; devolve quantos."""
    now = clock.now()
    cycles = await queue.stuck(now - limits.timeout - REAP_GRACE)
    for job in cycles:
        incident = await repository.get_incident(job.incident_id)
        if incident is None:  # o ciclo referencia o incidente: só acontece com dado corrompido
            raise LookupError(f"incidente {job.incident_id} do ciclo não existe")
        decision = decide_cycle(
            incident,
            cycle=partial_cycle(job, PartialReason.INTERRUPTED, (), now),
            history=(),
            proposals=(),
            impact=None,
            now=now,
            policy=policy,
            page_url=str,  # ciclo parcial não posta card: o link da página não é usado
        )
        await queue.finish(decision.cycle, decision.events, decision.notifications, (), now)
    return len(cycles)


def impact_text(value: float, currency: str) -> str:
    """ "R$ 3.000,00 estimado" (formato brasileiro); outra moeda vai com o código
    ("USD 3.000,00 estimado").
    """
    number = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    symbol = "R$" if currency == "BRL" else currency
    return f"{symbol} {number} estimado"


def _impact(incident: Incident, cycles: Sequence[Cycle], impact: ImpactPolicy) -> str | None:
    """O impacto dos ciclos, com o valor por minuto somado dos serviços configurados do
    incidente; None quando nenhum serviço dele tem valor.
    """
    rates = [impact.per_minute[s] for s in incident.services if s in impact.per_minute]
    if not rates:
        return None
    value = estimated_impact(cycles, incident.detected_at, sum(rates))
    return impact_text(value, impact.currency)


async def impact_of(
    repository: IncidentRepository, incident: Incident, impact: ImpactPolicy
) -> str | None:
    """O impacto estimado do incidente para a página; None sem valor configurado para os
    serviços dele.
    """
    return _impact(incident, await repository.cycles_of(incident.id), impact)
