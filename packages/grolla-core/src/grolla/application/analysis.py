"""Fila e casos de uso da análise inicial e da validação: todo pedido termina, concluído ou
parcial.
"""

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
    CloudInventory,
    ClusterInventory,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LlmUsage,
    Telemetry,
    TokenBudget,
    Toolbox,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder, memory_for
from grolla.domain.analysis import (
    PARTIAL_REASON_TEXT,
    Analysis,
    AnalysisStatus,
    NotConcreteError,
    PartialReason,
    Verification,
    complete,
    partial,
    verification_partial,
    verified,
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
        uma vez só. Quando a análise tem hipóteses, pede a validação na mesma transação
        (pendente desde `at`, com o evento `verification_requested` entre os `events`).
        """
        ...

    async def claim_verification(self, now: datetime) -> AnalysisJob | None:
        """Pega a validação pendente mais antiga e a marca em andamento; None se não há."""
        ...

    async def stuck_verifications(self, started_before: datetime) -> list[AnalysisJob]:
        """Validações em andamento desde antes de `started_before`."""
        ...

    async def finish_verification(
        self,
        verification: Verification,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Como `finish`, para a validação: grava tudo numa transação, só se em andamento."""
        ...


@dataclass(frozen=True)
class AnalysisPolicy:
    timeout: timedelta
    max_tokens: int
    lookback: timedelta  # janela das ferramentas
    page_url: Callable[[UUID], str]
    telemetry_url: str | None  # botão "ver na origem" do card de análise
    verification_timeout: timedelta = timedelta(minutes=5)
    verification_max_tokens: int = 150_000
    chat_timeout: timedelta = timedelta(minutes=2)
    chat_max_tokens: int = 60_000  # por resposta
    chat_max_tokens_per_incident: int = 400_000


REAP_GRACE = timedelta(minutes=2)  # folga além do timeout antes de dar a análise por perdida
NO_HYPOTHESIS = "nenhuma hipótese com evidência suficiente"
NO_ROOT_CAUSE = "causa raiz não confirmada"


async def run_next_analysis(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: AnalysisQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
    cloud: CloudInventory | None = None,
    cluster: ClusterInventory | None = None,
    memory: MemoryStore | None = None,
    embedder: TextEmbedder | None = None,
) -> bool:
    """Executa o próximo pedido de análise; False se não havia pedido.

    Qualquer falha da investigação vira análise parcial: o card de análise sempre sai. Com a
    `memory`, o modelo recebe a memória do incidente e pode apontar "já vi isso".
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    incident = await _incident(repository, job.incident_id)
    brief = await investigation_brief(repository, memory, incident, clock, embedder)
    toolbox = Toolbox(
        telemetry,
        changes,
        clock,
        policy.lookback,
        cloud,
        cluster=cluster,
        memory=memory,
        embedder=embedder,
    )
    budget = TokenBudget(policy.max_tokens)
    deadline = asyncio.timeout(policy.timeout.total_seconds())
    pending = _running(job)
    try:
        async with deadline:
            draft = await model.investigate(brief, toolbox, budget)
    except Exception as error:  # qualquer falha vira parcial: a análise sempre chega
        reason = failure_reason(error, timed_out=deadline.expired())
        analysis = partial(pending, reason, toolbox.evaluations, clock.now())
    else:
        episodes = brief.memory.episodes
        analysis = complete(pending, draft, toolbox.evaluations, clock.now(), episodes)
    await _finish(queue, incident, analysis, budget.usages, policy)
    return True


async def investigation_brief(
    repository: IncidentRepository,
    memory: MemoryStore | None,
    incident: Incident,
    clock: Clock,
    embedder: TextEmbedder | None = None,
) -> InvestigationBrief:
    """O incidente, os alertas e a memória sobre ele (busca híbrida com `embedder`)."""
    alerts = tuple(await repository.alerts_of(incident.id))
    context = await memory_for(memory, incident, alerts, embedder)
    return InvestigationBrief(incident, alerts, now=clock.now(), memory=context)


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


def failure_reason(error: Exception, *, timed_out: bool) -> PartialReason:
    """Por que a investigação não terminou; também é o motivo de uma resposta do chat falhar."""
    if isinstance(error, TimeoutError) and timed_out:
        return PartialReason.TIMEOUT
    if isinstance(error, BudgetExceededError):
        return PartialReason.BUDGET
    if isinstance(error, InvalidAnalysisError | NotConcreteError):
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
    events = [IncidentEvent(incident.id, now, kind, data)]
    if analysis.hypotheses:  # a fila cria o pedido da validação na mesma transação
        requested = {"hypotheses": len(analysis.hypotheses)}
        events.append(IncidentEvent(incident.id, now, EventType.VERIFICATION_REQUESTED, requested))
    events.append(
        IncidentEvent(incident.id, now, EventType.NOTIFICATION_QUEUED, {"kind": card.kind.value})
    )
    await queue.finish(analysis, events, card, usages, now)


def _main_evidence(analysis: Analysis) -> str:
    """A hipótese principal; na parcial, o motivo."""
    if analysis.partial_reason is not None:
        return f"análise parcial: {PARTIAL_REASON_TEXT[analysis.partial_reason]}"
    if analysis.hypotheses:
        return analysis.hypotheses[0].title
    return NO_HYPOTHESIS


async def run_next_verification(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: AnalysisQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
    cloud: CloudInventory | None = None,
    cluster: ClusterInventory | None = None,
    memory: MemoryStore | None = None,
    embedder: TextEmbedder | None = None,
) -> bool:
    """Executa a próxima validação pedida; False se não havia. Toda falha vira parcial.

    Plano com passo de investigação (NotConcreteError) é resultado inválido, como um rascunho
    fora do schema.
    """
    job = await queue.claim_verification(clock.now())
    if job is None:
        return False
    incident = await _incident(repository, job.incident_id)
    analysis = await repository.analysis_of(incident.id)
    if analysis is None:  # a validação nasce do fim da análise: só com dado corrompido
        raise LookupError(f"incidente {incident.id} tem validação sem análise inicial")
    brief = await investigation_brief(repository, memory, incident, clock, embedder)
    toolbox = Toolbox(
        telemetry,
        changes,
        clock,
        policy.lookback,
        cloud,
        cluster=cluster,
        memory=memory,
        embedder=embedder,
    )
    budget = TokenBudget(policy.verification_max_tokens)
    deadline = asyncio.timeout(policy.verification_timeout.total_seconds())
    pending = _running_verification(job)
    try:
        async with deadline:
            draft = await model.verify(brief, analysis, toolbox, budget)
        verification = verified(pending, draft, toolbox.evaluations, clock.now())
    except Exception as error:  # qualquer falha vira parcial: o card da causa raiz sempre sai
        reason = failure_reason(error, timed_out=deadline.expired())
        verification = verification_partial(pending, reason, toolbox.evaluations, clock.now())
    await _finish_verification(queue, incident, verification, budget.usages, policy)
    return True


async def reap_stuck_verifications(
    queue: AnalysisQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int:
    """Encerra como parciais (interrompidas) as validações presas; devolve quantas."""
    now = clock.now()
    jobs = await queue.stuck_verifications(now - policy.verification_timeout - REAP_GRACE)
    for job in jobs:
        incident = await _incident(repository, job.incident_id)
        pending = _running_verification(job)
        verification = verification_partial(pending, PartialReason.INTERRUPTED, (), now)
        await _finish_verification(queue, incident, verification, (), policy)
    return len(jobs)


def _running_verification(job: AnalysisJob) -> Verification:
    return Verification(
        incident_id=job.incident_id,
        status=AnalysisStatus.RUNNING,
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=None,
        verdicts=(),
        root_cause=None,
        actions=(),
        missing="",
        evaluations=(),
        partial_reason=None,
    )


async def _finish_verification(
    queue: AnalysisQueue,
    incident: Incident,
    verification: Verification,
    usages: Sequence[LlmUsage],
    policy: AnalysisPolicy,
) -> None:
    """Evento, card da causa raiz e gravação da validação encerrada (concluída ou parcial)."""
    now = verification.finished_at or verification.requested_at  # sempre preenchido
    completed = verification.status is AnalysisStatus.COMPLETED
    reason = verification.partial_reason
    data = {
        "status": verification.status.value,
        "reason": None if reason is None else reason.value,
        "root_cause": verification.root_cause,
        "verdicts": len(verification.verdicts),
        "actions": len(verification.actions),
        "evaluations": len(verification.evaluations),
    }
    kind = EventType.VERIFICATION_COMPLETED if completed else EventType.VERIFICATION_PARTIAL
    card = Notification(
        kind=NotificationKind.ROOT_CAUSE,
        incident_id=incident.id,
        title=incident.title,
        severity=incident.severity,
        services=incident.services,
        summary=verification.root_cause or NO_ROOT_CAUSE,
        evidence=verification.actions[0].action if verification.actions else verification.missing,
        source_url=policy.telemetry_url,
        page_url=policy.page_url(incident.id),
        verification=verification,
    )
    events = [
        IncidentEvent(incident.id, now, kind, data),
        IncidentEvent(incident.id, now, EventType.NOTIFICATION_QUEUED, {"kind": card.kind.value}),
    ]
    await queue.finish_verification(verification, events, card, usages, now)
