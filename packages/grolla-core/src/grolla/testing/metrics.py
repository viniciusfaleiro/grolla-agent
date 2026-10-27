"""Fake do `MetricsReader` sobre o estado em memória dos incidentes e dos post mortems."""

from datetime import datetime
from uuid import UUID

from grolla.domain.incidents import EventType, IncidentState
from grolla.domain.metrics import MetricsFacts
from grolla.domain.post_mortem import PostMortemStatus
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.post_mortem import InMemoryPostMortemStore

APPROVED_OR_CLOSED = frozenset({PostMortemStatus.APPROVED, PostMortemStatus.CLOSED})


class InMemoryMetricsReader:
    """Lê os fatos do repositório e do store em memória.

    Aprovados usam o `status_at` do post mortem: a data da aprovação ou, se já foi encerrado, a
    da última transição (o encerramento), como no banco.
    """

    def __init__(
        self, repository: InMemoryIncidentRepository, post_mortems: InMemoryPostMortemStore
    ) -> None:
        self.repository = repository
        self.post_mortems = post_mortems

    async def facts(self, since: datetime) -> MetricsFacts:
        resolved = tuple(
            i.resolved_at
            for i in self.repository.incidents.values()
            if i.state is IncidentState.RESOLVED
            and i.resolved_at is not None
            and i.resolved_at >= since
        )
        approved = tuple(
            (pm.status_at, pm.evaluation)
            for pm in self.post_mortems.documents.values()
            if pm.status in APPROVED_OR_CLOSED
            and pm.status_at is not None
            and pm.status_at >= since
        )
        last: dict[UUID, tuple[datetime, bool]] = {}
        for event in self.repository.events:
            if event.type is not EventType.SEEN_BEFORE_ANSWERED:
                continue
            previous = last.get(event.incident_id)
            if previous is None or event.at >= previous[0]:
                last[event.incident_id] = (event.at, bool(event.data["same"]))
        answers = tuple(answer for answer in last.values() if answer[0] >= since)
        return MetricsFacts(resolved=resolved, approved=approved, answers=answers)
