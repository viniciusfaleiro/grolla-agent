"""Contagem das mensagens de cada pessoa no chat do war room e no console.

O agente grava com o próprio autor, então as respostas dele não contam para ninguém. Os índices
`(author, at)` das duas tabelas são da migração 0014.
"""

from datetime import datetime

import sqlalchemy as sa

from grolla.adapters.postgres.database import PostgresDatabase

SENT_SINCE = sa.text(
    "SELECT at FROM chat_messages WHERE author = :author AND at >= :since"
    " UNION ALL SELECT at FROM console_messages WHERE author = :author AND at >= :since"
    " ORDER BY at"
)


class PostgresMessageCounter:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def sent_since(self, author: str, since: datetime) -> list[datetime]:
        async with self._engine.connect() as connection:
            result = await connection.execute(SENT_SINCE, {"author": author, "since": since})
            return list(result.scalars())
