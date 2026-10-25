from datetime import UTC, datetime

import pytest
from grolla.adapters.postgres import PostgresDatabase, migrate

pytestmark = pytest.mark.integration


async def test_migrations_apply_on_empty_database_and_heartbeat_round_trips(
    database_url: str,
) -> None:
    migrate(database_url)
    migrate(database_url)  # idempotente: a segunda execução não faz nada
    database = PostgresDatabase(database_url)
    beat = datetime(2026, 10, 24, 3, 0, tzinfo=UTC)

    try:
        assert await database.ping()
        await database.record_heartbeat(beat)
        assert await database.latest_heartbeat() == beat
    finally:
        await database.dispose()


async def test_ping_is_false_when_database_is_unreachable() -> None:
    database = PostgresDatabase("postgresql://grolla:grolla@127.0.0.1:1/grolla")
    try:
        assert not await database.ping()
    finally:
        await database.dispose()
