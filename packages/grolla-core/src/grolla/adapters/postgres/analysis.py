"""Fila de análises e registro de uso do modelo de linguagem sobre a migração 0003.

A tabela `analyses` é ao mesmo tempo a fila (status `pendente`/`em_andamento`) e a projeção
do resultado, como a outbox.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import (
    PostgresUnitOfWork,
    evaluations_data,
    hypotheses_data,
)
from grolla.application.analysis import AnalysisJob
from grolla.application.investigation import LlmUsage
from grolla.domain.analysis import Analysis, AnalysisStatus, Verification
from grolla.domain.incidents import IncidentEvent, Notification


def _job(row: sa.Row[Any]) -> AnalysisJob:
    return AnalysisJob(
        incident_id=row.incident_id, requested_at=row.requested_at, started_at=row.started_at
    )


class PostgresAnalysisQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> AnalysisJob | None:
        # SKIP LOCKED: workers em paralelo pegam pedidos diferentes, sem esperar um ao outro.
        async with self._engine.begin() as connection:
            result = await connection.execute(
                sa.text(
                    "UPDATE analyses SET status = :running, started_at = :now"
                    " WHERE incident_id = (SELECT incident_id FROM analyses"
                    " WHERE status = :pending ORDER BY requested_at, incident_id"
                    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
                    " RETURNING incident_id, requested_at, started_at"
                ),
                {
                    "running": AnalysisStatus.RUNNING.value,
                    "pending": AnalysisStatus.PENDING.value,
                    "now": now,
                },
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, requested_at, started_at FROM analyses"
                    " WHERE status = :running AND started_at < :before"
                    " ORDER BY started_at, incident_id"
                ),
                {"running": AnalysisStatus.RUNNING.value, "before": started_before},
            )
            return [_job(row) for row in result]

    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE analyses SET status = :status, started_at = :started_at,"
                    " finished_at = :finished_at, summary = :summary,"
                    " evaluations = CAST(:evaluations AS jsonb),"
                    " hypotheses = CAST(:hypotheses AS jsonb), plan = CAST(:plan AS jsonb),"
                    " partial_reason = :partial_reason"
                    " WHERE incident_id = :incident_id AND status = :running"
                ),
                {
                    "incident_id": analysis.incident_id,
                    "running": AnalysisStatus.RUNNING.value,
                    "status": analysis.status.value,
                    "started_at": analysis.started_at,
                    "finished_at": analysis.finished_at,
                    "summary": analysis.summary,
                    "evaluations": json.dumps(evaluations_data(analysis.evaluations)),
                    "hypotheses": json.dumps(hypotheses_data(analysis.hypotheses)),
                    "plan": json.dumps(list(analysis.plan)),
                    "partial_reason": (
                        None if analysis.partial_reason is None else analysis.partial_reason.value
                    ),
                },
            )
            if updated.rowcount == 0:
                # Já encerrada (ex.: o reaper fechou como interrompida): um card só por análise.
                return
            uow = PostgresUnitOfWork(connection)
            await uow.append(events)
            await uow.enqueue(notification, at)
            if usages:
                await connection.execute(
                    sa.text(
                        "INSERT INTO llm_usage (incident_id, stage, model, input_tokens,"
                        " output_tokens, cache_read_tokens, cache_creation_tokens, at)"
                        " VALUES (:incident_id, :stage, :model, :input_tokens, :output_tokens,"
                        " :cache_read_tokens, :cache_creation_tokens, :at)"
                    ),
                    [
                        {
                            "incident_id": analysis.incident_id,
                            "stage": usage.stage,
                            "model": usage.model,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "cache_read_tokens": usage.cache_read_tokens,
                            "cache_creation_tokens": usage.cache_creation_tokens,
                            "at": at,
                        }
                        for usage in usages
                    ],
                )

    async def claim_verification(self, now: datetime) -> AnalysisJob | None:
        raise NotImplementedError

    async def stuck_verifications(self, started_before: datetime) -> list[AnalysisJob]:
        raise NotImplementedError

    async def finish_verification(
        self,
        verification: Verification,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        raise NotImplementedError
