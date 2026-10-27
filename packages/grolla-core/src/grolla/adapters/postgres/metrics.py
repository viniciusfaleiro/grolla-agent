"""Fatos das métricas do agente no PostgreSQL: uma leitura só por página.

Contrato das métricas do agente: o corpo é do grolla-persistencia.
"""

from datetime import datetime

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.domain.metrics import MetricsFacts


class PostgresMetricsReader:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def facts(self, since: datetime) -> MetricsFacts:
        raise NotImplementedError
