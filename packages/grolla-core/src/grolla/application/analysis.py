"""Fila e casos de uso da análise inicial: todo pedido termina, concluído ou parcial."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    BudgetExceededError,
    ChangeHistory,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LlmUsage,
    Telemetry,
    TokenBudget,
    Toolbox,
)
from grolla.domain.analysis import (
    PARTIAL_REASON_TEXT,
    Analysis,
    AnalysisStatus,
    PartialReason,
    complete,
    partial,
)
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    Notification,
    NotificationKind,
)


@dataclass(frozen=True)
class AnalysisJob:
    incident_id: UUID
    requested_at: datetime
    started_at: datetime


class AnalysisQueue(Protocol):
    async def claim(self, now: datetime) -> AnalysisJob | None:
        """Pega o pedido pendente mais antigo e o marca em andamento; None se não há."""
        ...

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        """Pedidos em andamento desde antes de `started_before` (worker caiu no meio)."""
        ...

    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Numa transação: grava a análise, os eventos, o card na outbox e o uso do modelo.

        Só grava se o pedido ainda está em andamento. Se já foi encerrado (por exemplo, pelo
        `reap_stuck_analyses` enquanto o worker estava fora), não grava nada: a análise chega
        uma vez só.
        """
        ...


@dataclass(frozen=True)
class AnalysisPolicy:
    timeout: timedelta
    max_tokens: int
    lookback: timedelta  # janela das ferramentas
    page_url: Callable[[UUID], str]
    telemetry_url: str | None  # botão "ver na origem" do card de análise


REAP_GRACE = timedelta(minutes=2)  # folga além do timeout antes de dar a análise por perdida
NO_HYPOTHESIS = "nenhuma hipótese com evidência suficiente"


async def run_next_analysis(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: AnalysisQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
) -> bool:
    """Executa o próximo pedido de análise; False se não havia pedido.

    Qualquer falha da investigação vira análise parcial: o card de análise sempre sai.
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    incident = await _incident(repository, job.incident_id)
    brief = InvestigationBrief(
        incident, tuple(await repository.alerts_of(incident.id)), now=clock.now()
    )
    toolbox = Toolbox(telemetry, changes, clock, policy.lookback)
    budget = TokenBudget(policy.max_tokens)
    deadline = asyncio.timeout(policy.timeout.total_seconds())
    pending = _running(job)
    try:
        async with deadline:
            draft = await model.investigate(brief, toolbox, budget)
    except Exception as error:  # qualquer falha vira parcial: a análise sempre chega
        reason = _reason(error, timed_out=deadline.expired())
        analysis = partial(pending, reason, toolbox.evaluations, clock.now())
    else:
        analysis = complete(pending, draft, toolbox.evaluations, clock.now())
    await _finish(queue, incident, analysis, budget.usages, policy)
    return True


async def reap_stuck_analyses(
    queue: AnalysisQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int:
    """Encerra como parciais (interrompidas) as análises presas; devolve quantas."""
    now = clock.now()
    jobs = await queue.stuck(now - policy.timeout - REAP_GRACE)
    for job in jobs:
        incident = await _incident(repository, job.incident_id)
        analysis = partial(_running(job), PartialReason.INTERRUPTED, (), now)
        await _finish(queue, incident, analysis, (), policy)
    return len(jobs)


async def _incident(repository: IncidentRepository, incident_id: UUID) -> Incident:
    incident = await repository.get_incident(incident_id)
    if incident is None:  # o pedido referencia o incidente: só acontece com dado corrompido
        raise LookupError(f"incidente {incident_id} do pedido de análise não existe")
    return incident


def _running(job: AnalysisJob) -> Analysis:
    return Analysis(
        incident_id=job.incident_id,
        status=AnalysisStatus.RUNNING,
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=None,
        summary="",
        evaluations=(),
        hypotheses=(),
        plan=(),
        partial_reason=None,
    )


def _reason(error: Exception, *, timed_out: bool) -> PartialReason:
    if isinstance(error, TimeoutError) and timed_out:
        return PartialReason.TIMEOUT
    if isinstance(error, BudgetExceededError):
        return PartialReason.BUDGET
    if isinstance(error, InvalidAnalysisError):
        return PartialReason.INVALID_RESULT
    return PartialReason.LLM_FAILED  # LanguageModelError e qualquer outra falha


async def _finish(
    queue: AnalysisQueue,
    incident: Incident,
    analysis: Analysis,
    usages: Sequence[LlmUsage],
    policy: AnalysisPolicy,
) -> None:
    """Evento, card e gravação da análise encerrada (concluída ou parcial)."""
    now = analysis.finished_at or analysis.requested_at  # complete/partial sempre preenchem
    completed = analysis.status is AnalysisStatus.COMPLETED
    reason = analysis.partial_reason
    data = {
        "status": analysis.status.value,
        "reason": None if reason is None else reason.value,
        "summary": analysis.summary,
        "hypotheses": len(analysis.hypotheses),
        "evaluations": len(analysis.evaluations),
    }
    kind = EventType.ANALYSIS_COMPLETED if completed else EventType.ANALYSIS_PARTIAL
    card = Notification(
        kind=NotificationKind.ANALYSIS_READY,
        incident_id=incident.id,
        title=incident.title,
        severity=incident.severity,
        services=incident.services,
        summary=analysis.summary,
        evidence=_main_evidence(analysis),
        source_url=policy.telemetry_url,
        page_url=policy.page_url(incident.id),
        analysis=analysis,
    )
    events = [
        IncidentEvent(incident.id, now, kind, data),
        IncidentEvent(incident.id, now, EventType.NOTIFICATION_QUEUED, {"kind": card.kind.value}),
    ]
    await queue.finish(analysis, events, card, usages, now)


def _main_evidence(analysis: Analysis) -> str:
    """A hipótese principal; na parcial, o motivo."""
    if analysis.partial_reason is not None:
        return f"análise parcial: {PARTIAL_REASON_TEXT[analysis.partial_reason]}"
    if analysis.hypotheses:
        return analysis.hypotheses[0].title
    return NO_HYPOTHESIS
