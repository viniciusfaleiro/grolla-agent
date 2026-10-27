"""Transcrição de call: upload, extração pelo modelo na fila e reprocessamento.

Contrato da transcrição de call: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import LanguageModel, LlmUsage
from grolla.domain.incidents import IncidentEvent
from grolla.domain.transcript import Transcript


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
    """Limpa, corta em `max_chars` e enfileira; InvalidActionError sem autor ou texto vazio,
    LookupError para incidente inexistente."""
    raise NotImplementedError


async def run_next_transcript(
    *,
    store: TranscriptStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: TranscriptPolicy,
) -> bool:
    """Extrai a próxima; falha vira TRANSCRIPT_FAILED com o motivo. False sem pendentes."""
    raise NotImplementedError


async def reap_stuck_transcripts(
    store: TranscriptStore, clock: Clock, policy: TranscriptPolicy
) -> int:
    raise NotImplementedError


async def retry_transcript(
    *, store: TranscriptStore, transcript_id: int, author: str, clock: Clock
) -> None:
    """Reprocessa uma que falhou; InvalidActionError sem autor ou se não falhou."""
    raise NotImplementedError
