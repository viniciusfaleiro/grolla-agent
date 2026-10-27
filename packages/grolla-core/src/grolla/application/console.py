"""Casos de uso do console de operação: perguntar, responder com prioridade menor, desenhar os
gráficos e abrir um incidente a partir da conversa.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from grolla.application.analysis import REAP_GRACE, failure_reason
from grolla.application.chat import CONTEXT_MESSAGES, REPLY_FAILED
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    SIGNALS,
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
    TokenBudget,
    Toolbox,
    ToolUnavailableError,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder, query_for
from grolla.domain.analysis import PARTIAL_REASON_TEXT, Evaluation, PartialReason
from grolla.domain.chat import AGENT_AUTHOR, ReplyStatus
from grolla.domain.console import (
    ChartRequest,
    ConsoleDraft,
    ConsoleMessage,
    ConsoleSession,
    session_title,
    valid_charts,
)
from grolla.domain.incidents import Incident, Severity, decide_manual
from grolla.domain.memory import MemoryContext, Source, SourceKind, memory_text
from grolla.domain.war_room import InvalidActionError

CONSOLE_LIMIT_REACHED = "limite diário do console atingido"
CONSOLE_ORIGIN = "console"  # a origem do incidente aberto a partir da conversa


@dataclass(frozen=True)
class ConsoleJob:
    message_id: int  # a pergunta
    session_id: UUID
    author: str
    text: str
    asked_at: datetime
    started_at: datetime


class ConsoleStore(Protocol):
    async def create_session(
        self, session: ConsoleSession, question: ConsoleMessage
    ) -> ConsoleMessage:
        """Cria a sessão com a primeira pergunta, pendente de resposta."""
        ...

    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage:
        """Pergunta nova numa sessão existente, pendente de resposta; atualiza a sessão."""
        ...

    async def sessions(self, limit: int) -> list[ConsoleSession]:
        """Mais recentes primeiro (pela última atividade)."""
        ...

    async def session(self, session_id: UUID) -> ConsoleSession | None: ...

    async def messages(self, session_id: UUID) -> list[ConsoleMessage]:
        """Em ordem cronológica."""
        ...

    async def link_incident(self, session_id: UUID, incident_id: UUID) -> None: ...

    async def claim(self, now: datetime) -> ConsoleJob | None: ...

    async def stuck(self, started_before: datetime) -> list[ConsoleJob]: ...

    async def answer(
        self, job: ConsoleJob, reply: ConsoleMessage, usages: Sequence[LlmUsage], at: datetime
    ) -> None:
        """Numa transação, só se a pergunta está em andamento: grava a resposta, marca a
        pergunta e grava o uso (etapa console).
        """
        ...

    async def tokens_since(self, since: datetime) -> int:
        """Tokens do console (etapa console) desde `since`."""
        ...


@dataclass(frozen=True)
class ConsolePolicy:
    max_tokens: int = 60_000
    daily_max_tokens: int = 500_000
    timeout: timedelta = timedelta(minutes=2)
    lookback: timedelta = timedelta(minutes=60)  # janela das ferramentas


async def ask_console(
    *, store: ConsoleStore, session_id: UUID | None, author: str, text: str, clock: Clock
) -> ConsoleSession:
    """Pergunta numa sessão (ou cria uma nova, com o título da pergunta); a pergunta fica
    pendente de resposta. Levanta InvalidActionError sem autor ou texto, e LookupError com
    sessão inexistente.
    """
    author, text = author.strip(), text.strip()
    if not author:
        raise InvalidActionError("informe o seu nome antes de escrever")
    if not text:
        raise InvalidActionError("escreva a pergunta")
    now = clock.now()
    if session_id is None:
        session = ConsoleSession(uuid4(), session_title(text), author, now, now)
        await store.create_session(session, _question(session.id, author, text, now))
        return session
    session = await store.session(session_id)
    if session is None:
        raise LookupError(f"sessão {session_id} do console não existe")
    await store.add_question(_question(session_id, author, text, now))
    return replace(session, updated_at=now)


def console_context(
    messages: Sequence[ConsoleMessage], now: datetime, memory: MemoryContext | None = None
) -> str:
    """Texto compacto com a hora, as últimas mensagens da sessão e a memória (como dado)."""
    lines = [f"Agora: {now.isoformat()}", "", f"Conversa (últimas {CONTEXT_MESSAGES} mensagens):"]
    lines += [f"- {m.at:%H:%M:%S} {m.author}: {m.text}" for m in messages[-CONTEXT_MESSAGES:]]
    if not messages:
        lines.append("- nenhuma mensagem")
    remembered = memory_text(memory or MemoryContext())
    if remembered:
        lines += ["", remembered]
    return "\n".join(lines)


async def run_next_console_reply(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    store: ConsoleStore,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    cloud: CloudInventory,
    clock: Clock,
    policy: ConsolePolicy,
    memory: MemoryStore | None = None,
    embedder: TextEmbedder | None = None,
) -> bool:
    """Responde a próxima pergunta do console; False se não havia. A resposta sempre chega.

    Com o teto diário (`daily_max_tokens`, desde a meia-noite UTC) atingido, responde
    CONSOLE_LIMIT_REACHED sem chamar o modelo. Com a `memory`, o modelo recebe a memória do
    ambiente e pode guardar nela, com `remember`, o que o time ensinar (fonte: o autor da
    pergunta, no chat, sem incidente). Qualquer falha vira "não consegui responder: <motivo>",
    com as consultas feitas. Os gráficos pedidos passam por `valid_charts`.
    """
    now = clock.now()
    job = await store.claim(now)
    if job is None:
        return False
    midnight = datetime.combine(now.astimezone(UTC).date(), time.min, tzinfo=UTC)
    if await store.tokens_since(midnight) >= policy.daily_max_tokens:
        await _answer(store, job, ConsoleDraft(CONSOLE_LIMIT_REACHED), clock.now(), ok=False)
        return True
    context = console_context(
        await store.messages(job.session_id), now, await _memory(memory, embedder, job.text)
    )
    toolbox = Toolbox(
        telemetry,
        changes,
        clock,
        policy.lookback,
        cloud,
        memory=memory,
        embedder=embedder,
        remember_as=Source(SourceKind.CHAT, job.author, None),  # None: sem incidente
    )
    budget = TokenBudget(policy.max_tokens)
    deadline = asyncio.timeout(policy.timeout.total_seconds())
    try:
        async with deadline:
            draft = await model.consult(context, job.text, toolbox, budget)
    except Exception as error:  # qualquer falha vira "não consegui responder": a resposta chega
        draft = ConsoleDraft(_failed(failure_reason(error, timed_out=deadline.expired())))
        ok = False
    else:
        ok = True
    await _answer(
        store,
        job,
        draft,
        clock.now(),
        ok=ok,
        evaluations=toolbox.evaluations,
        usages=budget.usages,
    )
    return True


async def reap_stuck_console_replies(
    store: ConsoleStore, clock: Clock, policy: ConsolePolicy
) -> int:
    """Responde "não consegui responder: interrompida" às perguntas presas; devolve quantas."""
    now = clock.now()
    jobs = await store.stuck(now - policy.timeout - REAP_GRACE)
    for job in jobs:
        await _answer(store, job, ConsoleDraft(_failed(PartialReason.INTERRUPTED)), now, ok=False)
    return len(jobs)


async def open_incident_from_console(  # noqa: PLR0913 — o incidente manual tem todos os campos
    *,
    store: ConsoleStore,
    repository: IncidentRepository,
    session_id: UUID,
    title: str,
    service: str,
    severity: Severity,
    author: str,
    clock: Clock,
    page_url: Callable[[UUID], str],
) -> Incident:
    """Abre o incidente manual (origem "console"), com o card e o pedido de análise numa
    transação, e liga a sessão a ele. Levanta ValueError (título, serviço ou autor vazios) e
    LookupError com sessão inexistente.
    """
    now = clock.now()
    decision = decide_manual(
        title=title,
        service=service,
        severity=severity,
        author=author,
        origin=CONSOLE_ORIGIN,
        now=now,
        page_url=page_url,
        new_id=uuid4,
    )
    incident = decision.incident
    if incident is None:  # decide_manual sempre abre o incidente
        raise RuntimeError("a abertura manual não devolveu o incidente")
    if await store.session(session_id) is None:
        raise LookupError(f"sessão {session_id} do console não existe")
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.append(decision.events)
        await uow.request_analysis(incident.id, now)
        for notification in decision.notifications:
            await uow.enqueue(notification, now)
    await store.link_incident(session_id, incident.id)
    return incident


async def chart_series(
    telemetry: Telemetry, chart: ChartRequest, clock: Clock
) -> list[tuple[datetime, float]]:
    """A série do gráfico na janela até agora; levanta ToolUnavailableError (inclusive para
    sinal desconhecido).
    """
    if chart.signal not in SIGNALS:
        raise ToolUnavailableError(f"sinal desconhecido: {chart.signal}")
    end = clock.now()
    start = end - timedelta(minutes=chart.minutes)
    return await telemetry.series(chart.service, chart.signal, start, end)


async def _memory(
    memory: MemoryStore | None, embedder: TextEmbedder | None, text: str
) -> MemoryContext:
    """A memória do ambiente para a pergunta: sem serviço, o dossiê do ambiente e os episódios
    que casam com o texto.
    """
    if memory is None:
        return MemoryContext()
    return await memory.context_for((), (), text, await query_for(embedder, text))


def _question(session_id: UUID, author: str, text: str, at: datetime) -> ConsoleMessage:
    return ConsoleMessage(id=0, session_id=session_id, author=author, text=text, at=at)


def _failed(reason: PartialReason) -> str:
    return f"{REPLY_FAILED}: {PARTIAL_REASON_TEXT[reason]}"


async def _answer(  # noqa: PLR0913 — a resposta reúne o texto, os gráficos, as consultas e o uso
    store: ConsoleStore,
    job: ConsoleJob,
    draft: ConsoleDraft,
    now: datetime,
    *,
    ok: bool,
    evaluations: Sequence[Evaluation] = (),
    usages: Sequence[LlmUsage] = (),
) -> None:
    reply = ConsoleMessage(
        id=0,
        session_id=job.session_id,
        author=AGENT_AUTHOR,
        text=draft.text,
        at=now,
        charts=valid_charts(draft.charts),
        evaluations=tuple(evaluations),
        reply_status=ReplyStatus.ANSWERED if ok else ReplyStatus.FAILED,
        reply_to=job.message_id,
    )
    await store.answer(job, reply, usages, now)
