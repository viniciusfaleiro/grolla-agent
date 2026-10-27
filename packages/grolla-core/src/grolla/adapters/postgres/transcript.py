"""Transcrições de call no PostgreSQL (migração 0013), com a fila de extração.

A transcrição é a própria fila: `status` vai de `pendente` a `em_andamento` e termina em
`concluida` ou `falhou` (com `reason`). `queued_at` é a posição na fila: o recebimento ou o
pedido de reprocessar. Os itens extraídos vão para o log de eventos do incidente e o uso do
modelo para `llm_usage`, na mesma transação que encerra a transcrição.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import insert_events
from grolla.application.investigation import LlmUsage
from grolla.application.transcript import TranscriptJob
from grolla.domain.incidents import IncidentEvent
from grolla.domain.transcript import Transcript

PENDING = "pendente"
RUNNING = "em_andamento"
DONE = "concluida"
FAILED = "falhou"

# SKIP LOCKED: workers em paralelo pegam transcrições diferentes, sem esperar um ao outro.
CLAIM_TRANSCRIPT = sa.text(
    "UPDATE transcripts SET status = :running, started_at = :now"
    " WHERE id = (SELECT id FROM transcripts WHERE status = :pending"
    " ORDER BY queued_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING id, incident_id, author, text, received_at, started_at"
)


def _transcript(row: sa.Row[Any]) -> Transcript:
    return Transcript(
        id=row.id,
        incident_id=row.incident_id,
        author=row.author,
        text=row.text,
        received_at=row.received_at,
    )


def _job(row: sa.Row[Any]) -> TranscriptJob:
    return TranscriptJob(transcript=_transcript(row), started_at=row.started_at)


class PostgresTranscriptStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def add(self, transcript: Transcript, event: IncidentEvent) -> Transcript:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                sa.text(
                    "INSERT INTO transcripts (incident_id, author, text, received_at, queued_at,"
                    " status) VALUES (:incident_id, :author, :text, :at, :at, :pending)"
                    " RETURNING id"
                ),
                {
                    "incident_id": transcript.incident_id,
                    "author": transcript.author,
                    "text": transcript.text,
                    "at": transcript.received_at,
                    "pending": PENDING,
                },
            )
            saved = replace(transcript, id=result.scalar_one())
            await insert_events(
                connection, [replace(event, data={**event.data, "transcript": saved.id})]
            )
            return saved

    async def get(self, transcript_id: int) -> Transcript | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, incident_id, author, text, received_at, started_at FROM transcripts"
                    " WHERE id = :id"
                ),
                {"id": transcript_id},
            )
            row = result.one_or_none()
            return None if row is None else _transcript(row)

    async def claim(self, now: datetime) -> TranscriptJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_TRANSCRIPT, {"running": RUNNING, "pending": PENDING, "now": now}
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[TranscriptJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, incident_id, author, text, received_at, started_at FROM transcripts"
                    " WHERE status = :running AND started_at < :before ORDER BY started_at, id"
                ),
                {"running": RUNNING, "before": started_before},
            )
            return [_job(row) for row in result]

    async def finish(
        self,
        job: TranscriptJob,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE transcripts SET status = :status, finished_at = :at, reason = :reason"
                    " WHERE id = :id AND status = :running"
                ),
                {
                    "id": job.transcript.id,
                    "running": RUNNING,
                    "status": DONE if reason is None else FAILED,
                    "reason": reason,
                    "at": at,
                },
            )
            if updated.rowcount == 0:
                # Já encerrada (ex.: o reaper a deu como falha): o resultado tardio se descarta.
                return
            await insert_events(connection, events)
            await record_usages(connection, job.transcript.incident_id, usages, at)

    async def retry(self, transcript_id: int, at: datetime) -> bool:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE transcripts SET status = :pending, queued_at = :at,"
                    " started_at = NULL, finished_at = NULL, reason = NULL"
                    " WHERE id = :id AND status = :failed"
                ),
                {"id": transcript_id, "pending": PENDING, "failed": FAILED, "at": at},
            )
            return updated.rowcount == 1
