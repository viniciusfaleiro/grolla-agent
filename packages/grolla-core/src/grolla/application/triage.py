"""Casos de uso da triagem: rodar a triagem do incidente novo e juntar dois incidentes.

Contrato de menos ruído: os corpos são do grolla-dominio.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import LanguageModel, LlmUsage
from grolla.application.memory_store import MemoryStore
from grolla.domain.incidents import Incident, IncidentEvent, Notification


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
    raise NotImplementedError


async def reap_stuck_triages(queue: TriageQueue, clock: Clock, policy: TriagePolicy) -> int:
    raise NotImplementedError


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
    raise NotImplementedError
