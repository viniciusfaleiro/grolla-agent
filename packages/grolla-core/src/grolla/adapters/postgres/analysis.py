"""Filas da análise inicial e da validação, e uso do modelo (migrações 0003 e 0004).

As tabelas `analyses` e `verifications` são ao mesmo tempo a fila (status `pendente`/
`em_andamento`) e a projeção do resultado, como a outbox.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import (
    PostgresUnitOfWork,
    actions_data,
    evaluations_data,
    hypotheses_data,
    verdicts_data,
)
from grolla.application.analysis import AnalysisJob
from grolla.application.investigation import LlmUsage
from grolla.domain.analysis import Analysis, AnalysisStatus, PartialReason, Verification
from grolla.domain.incidents import IncidentEvent, Notification

# SKIP LOCKED: workers em paralelo pegam pedidos diferentes, sem esperar um ao outro.
CLAIM_ANALYSIS = sa.text(
    "UPDATE analyses SET status = :running, started_at = :now"
    " WHERE incident_id = (SELECT incident_id FROM analyses"
    " WHERE status = :pending ORDER BY requested_at, incident_id"
    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING incident_id, requested_at, started_at"
)
CLAIM_VERIFICATION = sa.text(
    "UPDATE verifications SET status = :running, started_at = :now"
    " WHERE incident_id = (SELECT incident_id FROM verifications"
    " WHERE status = :pending ORDER BY requested_at, incident_id"
    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING incident_id, requested_at, started_at"
)
STUCK_ANALYSES = sa.text(
    "SELECT incident_id, requested_at, started_at FROM analyses"
    " WHERE status = :running AND started_at < :before"
    " ORDER BY started_at, incident_id"
)
STUCK_VERIFICATIONS = sa.text(
    "SELECT incident_id, requested_at, started_at FROM verifications"
    " WHERE status = :running AND started_at < :before"
    " ORDER BY started_at, incident_id"
)


def _job(row: sa.Row[Any]) -> AnalysisJob:
    return AnalysisJob(
        incident_id=row.incident_id, requested_at=row.requested_at, started_at=row.started_at
    )


def _reason(reason: PartialReason | None) -> str | None:
    return None if reason is None else reason.value


async def _record(
    connection: AsyncConnection,
    *,
    events: Sequence[IncidentEvent],
    notification: Notification,
    usages: Sequence[LlmUsage],
    at: datetime,
) -> None:
    """Eventos, card na outbox e uso do modelo, na transação de quem encerra a fase."""
    uow = PostgresUnitOfWork(connection)
    await uow.append(events)
    await uow.enqueue(notification, at)
    if not usages:
        return
    await connection.execute(
        sa.text(
            "INSERT INTO llm_usage (incident_id, stage, model, input_tokens,"
            " output_tokens, cache_read_tokens, cache_creation_tokens, at)"
            " VALUES (:incident_id, :stage, :model, :input_tokens, :output_tokens,"
            " :cache_read_tokens, :cache_creation_tokens, :at)"
        ),
        [
            {
                "incident_id": notification.incident_id,
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


class PostgresAnalysisQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def _claim(self, statement: sa.TextClause, now: datetime) -> AnalysisJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                statement,
                {
                    "running": AnalysisStatus.RUNNING.value,
                    "pending": AnalysisStatus.PENDING.value,
                    "now": now,
                },
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def _stuck(self, statement: sa.TextClause, before: datetime) -> list[AnalysisJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                statement, {"running": AnalysisStatus.RUNNING.value, "before": before}
            )
            return [_job(row) for row in result]

    async def claim(self, now: datetime) -> AnalysisJob | None:
        return await self._claim(CLAIM_ANALYSIS, now)

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        return await self._stuck(STUCK_ANALYSES, started_before)

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
                    "partial_reason": _reason(analysis.partial_reason),
                },
            )
            if updated.rowcount == 0:
                # Já encerrada (ex.: o reaper fechou como interrompida): um card só por análise.
                return
            if analysis.hypotheses:
                await connection.execute(
                    sa.text(
                        "INSERT INTO verifications (incident_id, status, requested_at)"
                        " VALUES (:incident_id, :status, :at)"
                        " ON CONFLICT (incident_id) DO NOTHING"
                    ),
                    {
                        "incident_id": analysis.incident_id,
                        "status": AnalysisStatus.PENDING.value,
                        "at": at,
                    },
                )
            await _record(
                connection,
                events=events,
                notification=notification,
                usages=usages,
                at=at,
            )

    async def claim_verification(self, now: datetime) -> AnalysisJob | None:
        return await self._claim(CLAIM_VERIFICATION, now)

    async def stuck_verifications(self, started_before: datetime) -> list[AnalysisJob]:
        return await self._stuck(STUCK_VERIFICATIONS, started_before)

    async def finish_verification(
        self,
        verification: Verification,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE verifications SET status = :status, started_at = :started_at,"
                    " finished_at = :finished_at, verdicts = CAST(:verdicts AS jsonb),"
                    " root_cause = :root_cause, actions = CAST(:actions AS jsonb),"
                    " missing = :missing, evaluations = CAST(:evaluations AS jsonb),"
                    " partial_reason = :partial_reason"
                    " WHERE incident_id = :incident_id AND status = :running"
                ),
                {
                    "incident_id": verification.incident_id,
                    "running": AnalysisStatus.RUNNING.value,
                    "status": verification.status.value,
                    "started_at": verification.started_at,
                    "finished_at": verification.finished_at,
                    "verdicts": json.dumps(verdicts_data(verification.verdicts)),
                    "root_cause": verification.root_cause,
                    "actions": json.dumps(actions_data(verification.actions)),
                    "missing": verification.missing,
                    "evaluations": json.dumps(evaluations_data(verification.evaluations)),
                    "partial_reason": _reason(verification.partial_reason),
                },
            )
            if updated.rowcount == 0:
                # Já encerrada pelo reaper: um card de causa raiz só por validação.
                return
            await _record(
                connection,
                events=events,
                notification=notification,
                usages=usages,
                at=at,
            )
