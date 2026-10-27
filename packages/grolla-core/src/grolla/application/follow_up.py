"""Casos de uso do acompanhamento da crise: agendar, rodar e destravar os ciclos, e o impacto
estimado para a página.

Contrato do agente que acompanha a crise: os corpos são do grolla-dominio.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder
from grolla.domain.follow_up import Cycle, FollowUpPolicy
from grolla.domain.incidents import Incident, IncidentEvent, Notification, Severity


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


async def schedule_cycles(
    queue: CycleQueue, clock: Clock, policy: FollowUpPolicy, limits: FollowUpLimits
) -> int:
    """Enfileira os ciclos devidos (`cycle_due`), sem passar do teto de tokens do incidente;
    devolve quantos.
    """
    raise NotImplementedError


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
    """Roda o próximo ciclo; False se não havia. Falha vira ciclo parcial; o incidente segue."""
    raise NotImplementedError


async def reap_stuck_cycles(
    queue: CycleQueue, repository: IncidentRepository, clock: Clock, limits: FollowUpLimits
) -> int:
    """Encerra como parciais (interrompidos) os ciclos presos; devolve quantos."""
    raise NotImplementedError


def impact_text(value: float, currency: str) -> str:
    """ "R$ 3.000,00 estimado" (formato brasileiro)."""
    raise NotImplementedError


async def impact_of(
    repository: IncidentRepository, incident: Incident, impact: ImpactPolicy
) -> str | None:
    """O impacto estimado do incidente para a página; None sem valor configurado para os
    serviços dele.
    """
    raise NotImplementedError
