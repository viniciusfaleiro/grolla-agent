"""Casos de uso das métricas do agente: responder o "já vi isso", avaliar o agente no post mortem
e agregar por período.

Contrato das métricas do agente: os corpos são do grolla-dominio.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.post_mortem import PostMortemStore
from grolla.domain.metrics import Granularity, MetricsFacts, PeriodMetrics
from grolla.domain.post_mortem import AgentEvaluation, PostMortem


class MetricsReader(Protocol):
    async def facts(self, since: datetime) -> MetricsFacts:
        """Incidentes resolvidos, post mortems aprovados ou encerrados (com a avaliação) e a
        última resposta do "já vi isso" de cada incidente, desde `since`.
        """
        ...


async def agent_metrics(
    reader: MetricsReader, clock: Clock, granularity: Granularity, count: int = 6
) -> tuple[PeriodMetrics, ...]:
    raise NotImplementedError


async def answer_seen_before(
    *, repository: IncidentRepository, incident_id: UUID, same: bool, author: str, clock: Clock
) -> None:
    """Grava SEEN_BEFORE_ANSWERED; levanta InvalidActionError sem autor ou sem "já vi isso" na
    análise, e IncidentNotFoundError.
    """
    raise NotImplementedError


async def evaluate_agent(
    *,
    store: PostMortemStore,
    incident_id: UUID,
    evaluation: AgentEvaluation,
    author: str,
    clock: Clock,
) -> PostMortem:
    raise NotImplementedError
