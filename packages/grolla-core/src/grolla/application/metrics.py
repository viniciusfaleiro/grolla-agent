"""Casos de uso das métricas do agente: responder o "já vi isso", avaliar o agente no post mortem
e agregar por período.
"""

from datetime import UTC, datetime, time
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.post_mortem import NO_POST_MORTEM, PostMortemNotFoundError, PostMortemStore
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.incidents import EventType, IncidentEvent
from grolla.domain.metrics import Granularity, MetricsFacts, PeriodMetrics, aggregate, periods
from grolla.domain.post_mortem import AgentEvaluation, PostMortem, evaluated
from grolla.domain.war_room import InvalidActionError


class MetricsReader(Protocol):
    async def facts(self, since: datetime) -> MetricsFacts:
        """Incidentes resolvidos, post mortems aprovados ou encerrados (com a avaliação), a
        última resposta do "já vi isso" de cada incidente e as respostas recusadas do chat e do
        console, desde `since`.
        """
        ...


async def agent_metrics(
    reader: MetricsReader, clock: Clock, granularity: Granularity, count: int = 6
) -> tuple[PeriodMetrics, ...]:
    """Os `count` últimos períodos, do mais recente ao mais antigo, com uma leitura só."""
    chosen = periods(clock.now().astimezone(UTC).date(), granularity, count)
    since = datetime.combine(chosen[-1].start, time(), tzinfo=UTC)
    return aggregate(await reader.facts(since), chosen)


AUTHOR_REQUIRED = "informe o seu nome antes de responder"
NO_SEEN_BEFORE = "a análise deste incidente não apontou um caso parecido"


async def answer_seen_before(
    *, repository: IncidentRepository, incident_id: UUID, same: bool, author: str, clock: Clock
) -> None:
    """Grava SEEN_BEFORE_ANSWERED com o incidente passado e o autor; levanta InvalidActionError
    sem autor ou sem "já vi isso" na análise, e IncidentNotFoundError.
    """
    author = author.strip()
    if not author:
        raise InvalidActionError(AUTHOR_REQUIRED)
    if await repository.get_incident(incident_id) is None:
        raise IncidentNotFoundError(f"incidente {incident_id} não existe")
    analysis = await repository.analysis_of(incident_id)
    if analysis is None or analysis.seen_before is None:
        raise InvalidActionError(NO_SEEN_BEFORE)
    data = {"past": str(analysis.seen_before.incident_id), "same": same}
    event = IncidentEvent(incident_id, clock.now(), EventType.SEEN_BEFORE_ANSWERED, data, author)
    async with repository.transaction() as uow:
        await uow.append([event])


async def evaluate_agent(
    *,
    store: PostMortemStore,
    incident_id: UUID,
    evaluation: AgentEvaluation,
    author: str,
    clock: Clock,
) -> PostMortem:
    """Grava a avaliação do agente; levanta PostMortemNotFoundError ou InvalidPostMortemError."""
    pm = await store.get(incident_id)
    if pm is None:
        raise PostMortemNotFoundError(NO_POST_MORTEM)
    updated = evaluated(pm, evaluation, author, clock.now())
    await store.save(updated, [])
    return updated
