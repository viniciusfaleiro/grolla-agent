"""Fake do `MetricsReader` sobre o estado em memória dos incidentes e dos post mortems.

Contrato das métricas do agente: o corpo é do grolla-dominio.
"""

from datetime import datetime

from grolla.domain.metrics import MetricsFacts
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.post_mortem import InMemoryPostMortemStore


class InMemoryMetricsReader:
    def __init__(
        self, repository: InMemoryIncidentRepository, post_mortems: InMemoryPostMortemStore
    ) -> None:
        self.repository = repository
        self.post_mortems = post_mortems

    async def facts(self, since: datetime) -> MetricsFacts:
        raise NotImplementedError
