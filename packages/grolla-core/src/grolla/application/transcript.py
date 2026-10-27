"""Transcrição de call: upload, extração pelo modelo na fila e reprocessamento.

A extração só registra: itens e o que a call disse das hipóteses viram eventos, e nem a análise
nem o estado do incidente mudam.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePath
from typing import Protocol
from uuid import UUID

from grolla.application.analysis import REAP_GRACE, failure_reason
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import LanguageModel, LlmUsage, TokenBudget
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.analysis import PARTIAL_REASON_TEXT, Analysis, PartialReason, Verification
from grolla.domain.incidents import EventType, Incident, IncidentEvent
from grolla.domain.transcript import Transcript, clean_transcript, transcript_events
from grolla.domain.war_room import InvalidActionError

ACCEPTED_SUFFIXES = (".txt", ".vtt", ".srt")
INTERRUPTED = PARTIAL_REASON_TEXT[PartialReason.INTERRUPTED]


@dataclass(frozen=True)
class TranscriptJob:
    transcript: Transcript
    started_at: datetime


class TranscriptStore(Protocol):
    async def add(self, transcript: Transcript, event: IncidentEvent) -> Transcript:
        """Grava a transcrição pendente e o evento TRANSCRIPT_RECEIVED numa transação; devolve
        com o id (o evento recebe o id em `data["transcript"]`)."""
        ...

    async def get(self, transcript_id: int) -> Transcript | None: ...

    async def claim(self, now: datetime) -> TranscriptJob | None:
        """A pendente mais antiga passa a em andamento."""
        ...

    async def stuck(self, started_before: datetime) -> list[TranscriptJob]: ...

    async def finish(
        self,
        job: TranscriptJob,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        """Numa transação, só se ainda em andamento: eventos, uso do modelo e o desfecho
        (`reason` None = concluída; senão falhou com o motivo)."""
        ...

    async def retry(self, transcript_id: int, at: datetime) -> bool:
        """Uma transcrição que falhou volta a pendente; False se não falhou."""
        ...


@dataclass(frozen=True)
class TranscriptPolicy:
    max_chars: int = 200_000
    max_tokens: int = 80_000
    timeout: timedelta = timedelta(minutes=3)


async def upload_transcript(  # noqa: PLR0913 — caso de uso: tudo entra por parâmetro
    *,
    repository: IncidentRepository,
    store: TranscriptStore,
    incident_id: UUID,
    author: str,
    raw: str,
    filename: str | None,
    clock: Clock,
    policy: TranscriptPolicy,
) -> Transcript:
    """Limpa, corta em `max_chars` e enfileira; InvalidActionError sem autor, com texto vazio
    ou arquivo de outro tipo, IncidentNotFoundError (um LookupError) para incidente inexistente.

    O evento TRANSCRIPT_RECEIVED leva o tamanho guardado (`chars`) e se foi cortada
    (`truncated`), com a pessoa como autora.
    """
    author = author.strip()
    if not author:
        raise InvalidActionError("informe o seu nome antes de enviar a transcrição")
    if filename and PurePath(filename).suffix.lower() not in ACCEPTED_SUFFIXES:
        raise InvalidActionError("envie a transcrição em .txt, .vtt ou .srt")
    text = clean_transcript(raw, filename)
    if not text:
        raise InvalidActionError("a transcrição está vazia")
    if await repository.get_incident(incident_id) is None:
        raise IncidentNotFoundError(f"incidente {incident_id} não existe")
    truncated = len(text) > policy.max_chars
    text = text[: policy.max_chars]
    now = clock.now()
    data = {"chars": len(text), "truncated": truncated}
    event = IncidentEvent(incident_id, now, EventType.TRANSCRIPT_RECEIVED, data, author)
    return await store.add(Transcript(0, incident_id, author, text, now), event)


def transcript_context(
    incident: Incident, analysis: Analysis | None, verification: Verification | None
) -> str:
    """O que a extração precisa saber do incidente: título, serviços, estado, as hipóteses da
    última análise (uma por linha, "- <hipótese>", sob "Hipóteses da análise:") e o que a
    validação concluiu.
    """
    hypotheses = [] if analysis is None else [f"- {h.title}" for h in analysis.hypotheses]
    lines = [
        f"Incidente: {incident.title}",
        f"Serviços: {', '.join(incident.services)}",
        f"Estado: {incident.state.value}",
        "Hipóteses da análise:" if hypotheses else "Hipóteses da análise: nenhuma",
        *hypotheses,
    ]
    if verification is not None:
        lines.append(f"Causa raiz da validação: {verification.root_cause or 'não confirmada'}")
    return "\n".join(lines)


async def run_next_transcript(
    *,
    store: TranscriptStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: TranscriptPolicy,
) -> bool:
    """Extrai a próxima; falha vira TRANSCRIPT_FAILED com o motivo. False sem pendentes."""
    job = await store.claim(clock.now())
    if job is None:
        return False
    transcript = job.transcript
    incident = await repository.get_incident(transcript.incident_id)
    if incident is None:  # a transcrição referencia o incidente: só com dado corrompido
        raise LookupError(f"incidente {transcript.incident_id} da transcrição não existe")
    context = transcript_context(
        incident,
        await repository.analysis_of(incident.id),
        await repository.verification_of(incident.id),
    )
    budget = TokenBudget(policy.max_tokens)
    deadline = asyncio.timeout(policy.timeout.total_seconds())
    try:
        async with deadline:
            draft = await model.extract_transcript(context, transcript.text, budget)
    except Exception as error:  # qualquer falha fica registrada e pode ser reprocessada
        reason = PARTIAL_REASON_TEXT[failure_reason(error, timed_out=deadline.expired())]
        await _fail(store, job, reason, clock, budget.usages)
        return True
    now = clock.now()
    events = transcript_events(incident.id, transcript, draft, now)
    await store.finish(job, events, budget.usages, None, now)
    return True


async def _fail(
    store: TranscriptStore,
    job: TranscriptJob,
    reason: str,
    clock: Clock,
    usages: Sequence[LlmUsage] = (),
) -> None:
    now = clock.now()
    data = {"transcript": job.transcript.id, "reason": reason}
    failed = IncidentEvent(job.transcript.incident_id, now, EventType.TRANSCRIPT_FAILED, data)
    await store.finish(job, [failed], usages, reason, now)


async def reap_stuck_transcripts(
    store: TranscriptStore, clock: Clock, policy: TranscriptPolicy
) -> int:
    """Dá como falhas (interrompidas) as extrações presas além do timeout; devolve quantas."""
    jobs = await store.stuck(clock.now() - policy.timeout - REAP_GRACE)
    for job in jobs:
        await _fail(store, job, INTERRUPTED, clock)
    return len(jobs)


async def retry_transcript(
    *, store: TranscriptStore, transcript_id: int, author: str, clock: Clock
) -> None:
    """Reprocessa uma que falhou; InvalidActionError sem autor ou se não falhou."""
    if not author.strip():
        raise InvalidActionError("informe o seu nome antes de reprocessar")
    if not await store.retry(transcript_id, clock.now()):
        raise InvalidActionError("só uma transcrição que falhou pode ser reprocessada")
