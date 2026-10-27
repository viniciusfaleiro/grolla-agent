"""Console de operação no PostgreSQL (migração 0012): sessões, mensagens e a fila de respostas.

Como no chat do war room, a pergunta é a própria fila: `reply_status` vai de `pendente` a
`em_andamento` e termina em `respondida` ou `falhou` quando a resposta é gravada apontando
para ela. O uso do modelo fica em `llm_usage` na etapa `console`, sem incidente.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import evaluations_data, evaluations_from
from grolla.application.console import ConsoleJob
from grolla.application.investigation import STAGE_CONSOLE, LlmUsage
from grolla.domain.chat import ReplyStatus
from grolla.domain.console import ChartRequest, ConsoleMessage, ConsoleSession

# SKIP LOCKED: workers em paralelo pegam perguntas diferentes, sem esperar um ao outro.
CLAIM_QUESTION = sa.text(
    "UPDATE console_messages SET reply_status = :running, started_at = :now"
    " WHERE id = (SELECT id FROM console_messages WHERE reply_status = :pending"
    " ORDER BY at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING id, session_id, author, text, at, started_at"
)


def _session(row: sa.Row[Any]) -> ConsoleSession:
    return ConsoleSession(
        id=row.id,
        title=row.title,
        author=row.author,
        created_at=row.created_at,
        updated_at=row.updated_at,
        incident_id=row.incident_id,
    )


def _message(row: sa.Row[Any]) -> ConsoleMessage:
    return ConsoleMessage(
        id=row.id,
        session_id=row.session_id,
        author=row.author,
        text=row.text,
        at=row.at,
        charts=tuple(
            ChartRequest(service=c["service"], signal=c["signal"], minutes=c["minutes"])
            for c in row.charts
        ),
        evaluations=evaluations_from(row.evaluations),
        reply_status=None if row.reply_status is None else ReplyStatus(row.reply_status),
        reply_to=row.reply_to,
    )


def _job(row: sa.Row[Any]) -> ConsoleJob:
    return ConsoleJob(
        message_id=row.id,
        session_id=row.session_id,
        author=row.author,
        text=row.text,
        asked_at=row.at,
        started_at=row.started_at,
    )


async def _insert_message(
    connection: AsyncConnection,
    message: ConsoleMessage,
    reply_status: ReplyStatus | None,
    reply_to: int | None = None,
) -> ConsoleMessage:
    """Grava a mensagem (o `id` dela é ignorado) e a devolve com o id gerado pelo banco."""
    charts = [
        {"service": c.service, "signal": c.signal, "minutes": c.minutes} for c in message.charts
    ]
    result = await connection.execute(
        sa.text(
            "INSERT INTO console_messages (session_id, author, text, at, charts, evaluations,"
            " reply_status, reply_to) VALUES (:session_id, :author, :text, :at,"
            " CAST(:charts AS jsonb), CAST(:evaluations AS jsonb), :reply_status, :reply_to)"
            " RETURNING id, session_id, author, text, at, charts, evaluations, reply_status,"
            " reply_to"
        ),
        {
            "session_id": message.session_id,
            "author": message.author,
            "text": message.text,
            "at": message.at,
            "charts": json.dumps(charts),
            "evaluations": json.dumps(evaluations_data(message.evaluations)),
            "reply_status": None if reply_status is None else reply_status.value,
            "reply_to": reply_to,
        },
    )
    return _message(result.one())


async def _touch(connection: AsyncConnection, session_id: UUID, at: datetime) -> None:
    await connection.execute(
        sa.text(
            "UPDATE console_sessions SET updated_at = greatest(updated_at, :at) WHERE id = :id"
        ),
        {"id": session_id, "at": at},
    )


class PostgresConsoleStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def create_session(
        self, session: ConsoleSession, question: ConsoleMessage
    ) -> ConsoleMessage:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO console_sessions (id, title, author, created_at, updated_at,"
                    " incident_id) VALUES (:id, :title, :author, :created_at, :updated_at,"
                    " :incident_id)"
                ),
                {
                    "id": session.id,
                    "title": session.title,
                    "author": session.author,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "incident_id": session.incident_id,
                },
            )
            await _touch(connection, session.id, question.at)
            return await _insert_message(connection, question, ReplyStatus.PENDING)

    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage:
        async with self._engine.begin() as connection:
            await _touch(connection, question.session_id, question.at)
            return await _insert_message(connection, question, ReplyStatus.PENDING)

    async def sessions(self, limit: int) -> list[ConsoleSession]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, title, author, created_at, updated_at, incident_id"
                    " FROM console_sessions ORDER BY updated_at DESC, created_at DESC LIMIT :limit"
                ),
                {"limit": limit},
            )
            return [_session(row) for row in result]

    async def session(self, session_id: UUID) -> ConsoleSession | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, title, author, created_at, updated_at, incident_id"
                    " FROM console_sessions WHERE id = :id"
                ),
                {"id": session_id},
            )
            row = result.one_or_none()
            return None if row is None else _session(row)

    async def messages(self, session_id: UUID) -> list[ConsoleMessage]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, session_id, author, text, at, charts, evaluations,"
                    " reply_status, reply_to FROM console_messages WHERE session_id = :id"
                    " ORDER BY at, id"
                ),
                {"id": session_id},
            )
            return [_message(row) for row in result]

    async def link_incident(self, session_id: UUID, incident_id: UUID) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text("UPDATE console_sessions SET incident_id = :incident WHERE id = :id"),
                {"id": session_id, "incident": incident_id},
            )

    async def claim(self, now: datetime) -> ConsoleJob | None:
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

    async def stuck(self, started_before: datetime) -> list[ConsoleJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, session_id, author, text, at, started_at FROM console_messages"
                    " WHERE reply_status = :running AND started_at < :before"
                    " ORDER BY started_at, id"
                ),
                {"running": ReplyStatus.RUNNING.value, "before": started_before},
            )
            return [_job(row) for row in result]

    async def answer(
        self, job: ConsoleJob, reply: ConsoleMessage, usages: Sequence[LlmUsage], at: datetime
    ) -> None:
        # O desfecho vem em `reply.reply_status` (None conta como respondida) e fica na pergunta.
        outcome = reply.reply_status or ReplyStatus.ANSWERED
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE console_messages SET reply_status = :outcome"
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
            await _insert_message(connection, reply, reply_status=None, reply_to=job.message_id)
            await record_usages(connection, None, usages, at)

    async def tokens_since(self, since: datetime) -> int:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                # Todos os tokens processados, como conta o TokenBudget.
                sa.text(
                    "SELECT coalesce(sum(input_tokens + output_tokens + cache_read_tokens"
                    " + cache_creation_tokens), 0) FROM llm_usage"
                    " WHERE stage = :stage AND at >= :since"
                ),
                {"stage": STAGE_CONSOLE, "since": since},
            )
            return int(result.scalar_one())
