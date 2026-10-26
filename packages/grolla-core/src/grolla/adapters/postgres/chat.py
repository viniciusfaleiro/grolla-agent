"""Fila das perguntas do chat do war room (migração 0005).

A pergunta de uma pessoa é a própria fila: `reply_status` vai de `pendente` a `em_andamento` e
termina em `respondida` ou `falhou` quando a resposta do agente é gravada apontando para ela.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import insert_events, insert_message
from grolla.application.chat import ChatJob
from grolla.application.investigation import STAGE_WARROOM_CHAT, LlmUsage
from grolla.domain.chat import ChatMessage, ReplyStatus
from grolla.domain.incidents import IncidentEvent

# SKIP LOCKED: workers em paralelo pegam perguntas diferentes, sem esperar um ao outro.
CLAIM_QUESTION = sa.text(
    "UPDATE chat_messages SET reply_status = :running, started_at = :now"
    " WHERE id = (SELECT id FROM chat_messages WHERE reply_status = :pending"
    " ORDER BY at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING id, incident_id, author, text, at, started_at"
)


def _job(row: sa.Row[Any]) -> ChatJob:
    return ChatJob(
        message_id=row.id,
        incident_id=row.incident_id,
        author=row.author,
        text=row.text,
        asked_at=row.at,
        started_at=row.started_at,
    )


class PostgresChatQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> ChatJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_QUESTION,
                {
                    "running": ReplyStatus.RUNNING.value,
                    "pending": ReplyStatus.PENDING.value,
                    "now": now,
                },
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[ChatJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, incident_id, author, text, at, started_at FROM chat_messages"
                    " WHERE reply_status = :running AND started_at < :before"
                    " ORDER BY started_at, id"
                ),
                {"running": ReplyStatus.RUNNING.value, "before": started_before},
            )
            return [_job(row) for row in result]

    async def answer(
        self,
        job: ChatJob,
        reply: ChatMessage,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        # O desfecho vem em `reply.reply_status` (None conta como respondida) e fica na pergunta.
        outcome = reply.reply_status or ReplyStatus.ANSWERED
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE chat_messages SET reply_status = :outcome"
                    " WHERE id = :id AND reply_status = :running"
                ),
                {
                    "id": job.message_id,
                    "running": ReplyStatus.RUNNING.value,
                    "outcome": outcome.value,
                },
            )
            if updated.rowcount == 0:
                # Já respondida (ex.: o reaper respondeu "interrompida"): uma resposta só.
                return
            await insert_message(connection, reply, reply_status=None, reply_to=job.message_id)
            await insert_events(connection, events)
            await record_usages(connection, job.incident_id, usages, at)

    async def chat_tokens(self, incident_id: UUID) -> int:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                # Todos os tokens processados, como conta o TokenBudget.
                sa.text(
                    "SELECT coalesce(sum(input_tokens + output_tokens + cache_read_tokens"
                    " + cache_creation_tokens), 0) FROM llm_usage"
                    " WHERE incident_id = :id AND stage = :stage"
                ),
                {"id": incident_id, "stage": STAGE_WARROOM_CHAT},
            )
            return int(result.scalar_one())
