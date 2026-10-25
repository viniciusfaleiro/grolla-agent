from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from grolla.adapters.postgres.urls import sqlalchemy_url

PING_TIMEOUT_SECONDS = 2


class PostgresDatabase:
    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            sqlalchemy_url(database_url),
            pool_pre_ping=True,
            connect_args={"connect_timeout": PING_TIMEOUT_SECONDS},
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(sa.text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True

    async def record_heartbeat(self, at: datetime) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text("INSERT INTO heartbeats (beat_at) VALUES (:at)"), {"at": at}
            )

    async def latest_heartbeat(self) -> datetime | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(sa.text("SELECT max(beat_at) FROM heartbeats"))
            latest: datetime | None = result.scalar_one()
            return latest

    async def dispose(self) -> None:
        await self._engine.dispose()
