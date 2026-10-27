"""Casos de uso da triagem: rodar a triagem do incidente novo e juntar dois incidentes."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.analysis import REAP_GRACE, failure_reason
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    LanguageModel,
    LlmUsage,
    TokenBudget,
    TriageBrief,
)
from grolla.application.memory_store import MemoryStore, memory_for
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.analysis import PARTIAL_REASON_TEXT, PartialReason
from grolla.domain.incidents import OPEN_STATES, EventType, Incident, IncidentEvent, Notification
from grolla.domain.triage import (
    TriageDecision,
    TriageDraft,
    decide_merge,
    decide_triage,
    needs_model,
)

INTERRUPTED = PARTIAL_REASON_TEXT[PartialReason.INTERRUPTED]


@dataclass(frozen=True)
class TriageJob:
    incident_id: UUID
    requested_at: datetime
    started_at: datetime


class TriageQueue(Protocol):
    async def claim(self, now: datetime) -> TriageJob | None: ...

    async def stuck(self, started_before: datetime) -> list[TriageJob]: ...

    async def open_since(self, since: datetime) -> list[Incident]:
        """Incidentes abertos com o último alerta desde `since`."""
        ...

    async def finish(  # noqa: PLR0913 — o fim da triagem grava tudo de uma vez
        self,
        job: TriageJob,
        incident: Incident | None,
        *,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        """Numa transação, só se em andamento: grava o incidente (se mudou), os eventos, os cards
        e o uso (etapa triage), e encerra a triagem (falha com `reason`).
        """
        ...


@dataclass(frozen=True)
class TriagePolicy:
    window: timedelta = timedelta(minutes=30)
    max_tokens: int = 20_000
    timeout: timedelta = timedelta(minutes=1)


async def run_next_triage(  # noqa: PLR0913 — o caso de uso liga os ports da triagem
    *,
    queue: TriageQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: TriagePolicy,
    page_url: Callable[[UUID], str],
    memory: MemoryStore | None = None,
) -> bool:
    """Faz a próxima triagem pedida; False se não havia pedido.

    Sem outro incidente aberto de outro serviço na janela e com a severidade vinda de uma regra,
    termina sem chamar o modelo. Falha do modelo termina a triagem com o motivo, sem mudar o
    incidente.
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    incident = await repository.get_incident(job.incident_id)
    if incident is None or incident.state not in OPEN_STATES:  # encerrado antes da triagem
        await _end(queue, job, clock)
        return True
    others = [o for o in await queue.open_since(clock.now() - policy.window) if o.id != incident.id]
    events = await repository.events_of(incident.id)
    opened_by_default = any(
        e.type is EventType.ALERT_RECEIVED and e.data.get("severity_rule") is None for e in events
    )
    draft: TriageDraft | None = None
    budget = TokenBudget(policy.max_tokens)
    if needs_model(incident, others, opened_by_default):
        brief = await _brief(
            repository,
            memory,
            incident,
            others=others,
            opened_by_default=opened_by_default,
            clock=clock,
        )
        deadline = asyncio.timeout(policy.timeout.total_seconds())
        try:
            async with deadline:
                draft = await model.triage(brief, budget)
        except Exception as error:  # a triagem é só sugestão: o incidente segue como está
            reason = PARTIAL_REASON_TEXT[failure_reason(error, timed_out=deadline.expired())]
            await _end(queue, job, clock, usages=budget.usages, reason=reason)
            return True
        # Relê o incidente: um alerta agrupado durante a chamada não pode ser perdido.
        incident = await repository.get_incident(incident.id) or incident
    decision = decide_triage(
        incident,
        others=others,
        draft=draft,
        opened_by_default=opened_by_default,
        now=clock.now(),
        page_url=page_url,
    )
    await _end(queue, job, clock, decision=decision, usages=budget.usages)
    return True


async def _end(  # noqa: PLR0913 — o fim da triagem grava tudo de uma vez
    queue: TriageQueue,
    job: TriageJob,
    clock: Clock,
    *,
    decision: TriageDecision | None = None,
    usages: Sequence[LlmUsage] = (),
    reason: str | None = None,
) -> None:
    await queue.finish(
        job,
        None if decision is None else decision.incident,
        events=() if decision is None else decision.events,
        notifications=() if decision is None else decision.notifications,
        usages=usages,
        reason=reason,
        at=clock.now(),
    )


async def _brief(  # noqa: PLR0913 — o brief junta o incidente, os outros e a memória
    repository: IncidentRepository,
    memory: MemoryStore | None,
    incident: Incident,
    *,
    others: Sequence[Incident],
    opened_by_default: bool,
    clock: Clock,
) -> TriageBrief:
    """Os alertas do incidente, o resumo de cada outro e a memória dos serviços de todos."""
    alerts = tuple(await repository.alerts_of(incident.id))
    summaries: list[tuple[Incident, str]] = []
    for other in others:
        rules = dict.fromkeys(a.rule_name for a in await repository.alerts_of(other.id))
        analysis = await repository.analysis_of(other.id)
        summary = f"Alertas: {', '.join(rules) or 'nenhum'}"
        if analysis is not None and analysis.summary:
            summary += f"\n  Análise: {analysis.summary}"
        summaries.append((other, summary))
    services = (*incident.services, *(s for o in others for s in o.services))
    everyone = replace(incident, services=tuple(dict.fromkeys(services)))
    context = await memory_for(memory, everyone, alerts)
    return TriageBrief(
        incident, alerts, tuple(summaries), context, opened_by_default, now=clock.now()
    )


async def reap_stuck_triages(queue: TriageQueue, clock: Clock, policy: TriagePolicy) -> int:
    """Encerra como falhas (interrompidas) as triagens presas; devolve quantas."""
    now = clock.now()
    jobs = await queue.stuck(now - policy.timeout - REAP_GRACE)
    for job in jobs:
        await _end(queue, job, clock, reason=INTERRUPTED)
    return len(jobs)


async def merge_incidents(  # noqa: PLR0913 — juntar precisa dos dois, quem e quando
    *,
    repository: IncidentRepository,
    source_id: UUID,
    target_id: UUID,
    author: str,
    clock: Clock,
    page_url: Callable[[UUID], str],
) -> Incident:
    """Trava os dois (ordem por id), decide e grava; devolve o principal. Levanta
    InvalidActionError ou IncidentNotFoundError.
    """
    async with repository.transaction() as uow:
        locked = {i: await uow.incident_for_update(i) for i in sorted({source_id, target_id})}
        source, target = locked[source_id], locked[target_id]
        if source is None or target is None:
            missing = source_id if source is None else target_id
            raise IncidentNotFoundError(f"incidente {missing} não existe")
        now = clock.now()
        decision = decide_merge(source, target, author=author, now=now, page_url=page_url)
        await uow.save(decision.source)
        await uow.save(decision.target)
        await uow.append(decision.events)
        for notification in decision.notifications:
            await uow.enqueue(notification, now)
    return decision.target
