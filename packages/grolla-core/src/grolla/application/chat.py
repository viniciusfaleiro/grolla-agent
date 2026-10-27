"""Respostas do agente no chat do war room: fila, contexto e a garantia de que chegam."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from grolla.application.analysis import (
    REAP_GRACE,
    AnalysisPolicy,
    failure_reason,
    investigation_brief,
)
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    ChangeHistory,
    CloudInventory,
    ClusterInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
    TokenBudget,
    Toolbox,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder
from grolla.domain.analysis import (
    PARTIAL_REASON_TEXT,
    Analysis,
    Evaluation,
    PartialReason,
    Verification,
)
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, Incident, IncidentEvent
from grolla.domain.memory import MemoryContext, Source, SourceKind, memory_text

CHAT_LIMIT_REACHED = "limite do chat deste incidente atingido"
REPLY_FAILED = "não consegui responder"
CONTEXT_EVENTS = 30
CONTEXT_MESSAGES = 20


@dataclass(frozen=True)
class ChatJob:
    message_id: int  # a pergunta a responder
    incident_id: UUID
    author: str
    text: str
    asked_at: datetime
    started_at: datetime


class ChatQueue(Protocol):
    async def claim(self, now: datetime) -> ChatJob | None:
        """A pergunta pendente mais antiga, marcada em andamento; None se não há."""
        ...

    async def stuck(self, started_before: datetime) -> list[ChatJob]:
        """Perguntas em andamento desde antes de `started_before`."""
        ...

    async def answer(
        self,
        job: ChatJob,
        reply: ChatMessage,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Numa transação: grava a resposta, marca a pergunta respondida (ou falhou, conforme
        `reply`), os eventos e o uso. Só grava se a pergunta ainda está em andamento.
        """
        ...

    async def chat_tokens(self, incident_id: UUID) -> int:
        """Tokens já usados no chat do incidente (etapa warroom_chat)."""
        ...


def chat_context(  # noqa: PLR0913 — o contexto reúne tudo o que o war room já sabe
    incident: Incident,
    analysis: Analysis | None,
    verification: Verification | None,
    events: Sequence[IncidentEvent],
    messages: Sequence[ChatMessage],
    *,
    memory: MemoryContext | None = None,
) -> str:
    """Texto compacto com o incidente, a análise, a validação, a timeline, o chat e a
    memória (como dado).
    """
    lines = [
        f"Incidente: {incident.title}",
        f"Estado: {incident.state.value}",
        f"Severidade: {incident.severity.value}",
        f"IC: {incident.commander or 'ninguém assumiu'}",
        f"Serviços: {', '.join(incident.services)}",
        f"Detectado em: {incident.detected_at.isoformat()}",
        "",
        *_analysis_lines(analysis),
        "",
        *_verification_lines(verification),
        "",
        f"Timeline (últimos {CONTEXT_EVENTS} eventos):",
    ]
    lines += [
        f"- {e.at:%H:%M:%S} {e.author}: {e.type.value} {_data(e)}".rstrip()
        for e in events[-CONTEXT_EVENTS:]
    ]
    lines += ["", f"Chat (últimas {CONTEXT_MESSAGES} mensagens):"]
    lines += [f"- {m.at:%H:%M:%S} {m.author}: {m.text}" for m in messages[-CONTEXT_MESSAGES:]]
    if not messages:
        lines.append("- nenhuma mensagem")
    remembered = memory_text(memory or MemoryContext())
    if remembered:
        lines += ["", remembered]
    return "\n".join(lines)


def _analysis_lines(analysis: Analysis | None) -> list[str]:
    if analysis is None:
        return ["Análise inicial: não pedida"]
    lines = [f"Análise inicial ({analysis.status.value}): {analysis.summary or '-'}"]
    lines += [f"- Hipótese: {h.title} (confiança {h.confidence:.0%})" for h in analysis.hypotheses]
    return lines


def _verification_lines(verification: Verification | None) -> list[str]:
    if verification is None:
        return ["Validação: não pedida"]
    lines = [
        f"Validação ({verification.status.value})",
        f"Causa raiz: {verification.root_cause or 'não confirmada'}",
    ]
    lines += [
        f"- Veredito: {v.hypothesis}: {v.verdict.value} ({v.reasoning})"
        for v in verification.verdicts
    ]
    lines += [f"- Plano: {a.action} em {a.target}" for a in verification.actions]
    if verification.missing:
        lines.append(f"Faltou: {verification.missing}")
    return lines


def _data(event: IncidentEvent) -> str:
    return ", ".join(f"{key}={value}" for key, value in event.data.items())


async def run_next_reply(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: ChatQueue,
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
    """Responde a próxima pergunta; False se não havia. A resposta sempre chega.

    Com a `memory`, o modelo recebe a memória do incidente e pode guardar nela, com `remember`,
    o que o time explicar (fonte: o autor da pergunta, no chat do incidente).

    Com o teto de tokens do chat do incidente atingido, responde CHAT_LIMIT_REACHED sem chamar
    o modelo. Qualquer falha vira "não consegui responder: <motivo>", com as consultas feitas.
    """
    job = await queue.claim(clock.now())
    if job is None:
        return False
    if await queue.chat_tokens(job.incident_id) >= policy.chat_max_tokens_per_incident:
        await _answer(queue, job, CHAT_LIMIT_REACHED, clock.now(), ok=False)
        return True
    incident = await repository.get_incident(job.incident_id)
    if incident is None:  # a pergunta referencia o incidente: só acontece com dado corrompido
        raise LookupError(f"incidente {job.incident_id} da pergunta não existe")
    brief = await investigation_brief(repository, memory, incident, clock, embedder)
    context = chat_context(
        incident,
        await repository.analysis_of(incident.id),
        await repository.verification_of(incident.id),
        await repository.events_of(incident.id),
        await repository.chat_of(incident.id),
        memory=brief.memory,
    )
    toolbox = Toolbox(
        telemetry,
        changes,
        clock,
        policy.lookback,
        cloud,
        cluster=cluster,
        memory=memory,
        embedder=embedder,
        remember_as=Source(SourceKind.CHAT, job.author, str(incident.id)),
    )
    budget = TokenBudget(policy.chat_max_tokens)
    deadline = asyncio.timeout(policy.chat_timeout.total_seconds())
    try:
        async with deadline:
            draft = await model.reply(brief, context, job.text, toolbox, budget)
    except Exception as error:  # qualquer falha vira "não consegui responder": a resposta chega
        reason = failure_reason(error, timed_out=deadline.expired())
        text, ok = _failed(reason), False
    else:
        text, ok = draft.text, True
    await _answer(
        queue,
        job,
        text,
        clock.now(),
        ok=ok,
        evaluations=toolbox.evaluations,
        usages=budget.usages,
    )
    return True


async def reap_stuck_replies(
    queue: ChatQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int:
    """Responde "não consegui responder: interrompida" às perguntas presas; devolve quantas."""
    del repository  # a resposta de interrupção não precisa do incidente
    now = clock.now()
    jobs = await queue.stuck(now - policy.chat_timeout - REAP_GRACE)
    for job in jobs:
        await _answer(queue, job, _failed(PartialReason.INTERRUPTED), now, ok=False)
    return len(jobs)


def _failed(reason: PartialReason) -> str:
    return f"{REPLY_FAILED}: {PARTIAL_REASON_TEXT[reason]}"


async def _answer(  # noqa: PLR0913 — a resposta reúne o texto, as consultas e o uso
    queue: ChatQueue,
    job: ChatJob,
    text: str,
    now: datetime,
    *,
    ok: bool,
    evaluations: Sequence[Evaluation] = (),
    usages: Sequence[LlmUsage] = (),
) -> None:
    reply = ChatMessage(
        id=0,
        incident_id=job.incident_id,
        author=AGENT_AUTHOR,
        text=text,
        at=now,
        evaluations=tuple(evaluations),
        reply_status=ReplyStatus.ANSWERED if ok else ReplyStatus.FAILED,
        reply_to=job.message_id,
    )
    data = {"reply_to": job.message_id, "ok": ok, "text": text}
    event = IncidentEvent(job.incident_id, now, EventType.AGENT_MESSAGE, data, AGENT_AUTHOR)
    await queue.answer(job, reply, [event], usages, now)
