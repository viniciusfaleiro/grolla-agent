"""Fatos das métricas do agente sobre Postgres real.

A leitura vê todos os incidentes, post mortems e eventos do banco (e o log não aceita TRUNCATE),
então cada teste usa um banco próprio dentro do container da sessão.
"""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import sqlalchemy as sa
from contracts.metrics_reader_contract import MetricsReaderContract
from grolla.adapters.postgres import (
    PostgresDatabase,
    PostgresIncidentRepository,
    PostgresMetricsReader,
    PostgresPostMortemStore,
    migrate,
)
from grolla.adapters.postgres.urls import sqlalchemy_url
from grolla.application.incidents import IncidentRepository
from grolla.application.metrics import MetricsReader
from grolla.application.post_mortem import PostMortemStore
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture
async def database(database_url: str) -> AsyncIterator[PostgresDatabase]:
    name = f"metrics_{uuid4().hex[:8]}"
    admin = create_async_engine(sqlalchemy_url(database_url), isolation_level="AUTOCOMMIT")
    async with admin.connect() as connection:
        await connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    url = f"{database_url.rsplit('/', 1)[0]}/{name}"
    migrate(url)
    database = PostgresDatabase(url)
    yield database
    await database.dispose()


class TestPostgresMetricsReaderContract(MetricsReaderContract):
    @pytest.fixture
    def repository(self, database: PostgresDatabase) -> IncidentRepository:
        return PostgresIncidentRepository(database)

    @pytest.fixture
    def post_mortems(self, database: PostgresDatabase) -> PostMortemStore:
        return PostgresPostMortemStore(database)

    @pytest.fixture
    def reader(self, database: PostgresDatabase) -> MetricsReader:
        return PostgresMetricsReader(database)
