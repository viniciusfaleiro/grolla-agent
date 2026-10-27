"""Contagem das mensagens de cada pessoa no chat do war room e no console.

Contrato do agente cercado: os corpos são do grolla-persistencia.
"""

from datetime import datetime

from grolla.adapters.postgres.database import PostgresDatabase


class PostgresMessageCounter:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def sent_since(self, author: str, since: datetime) -> list[datetime]:
        raise NotImplementedError
