"""Fila da triagem dos incidentes novos (migração 0011).

A tabela `triages` é a fila (status `pendente`/`em_andamento`) e o registro de como a triagem
terminou (`feito` ou `falhou`, com o motivo).
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import (
    OPEN_STATE_VALUES,
    PostgresUnitOfWork,
    incident_from_row,
)
from grolla.application.investigation import LlmUsage
from grolla.application.triage import TriageJob
from grolla.domain.incidents import Incident, IncidentEvent, Notification

PENDING = "pendente"
RUNNING = "em_andamento"
DONE = "feito"
FAILED = "falhou"

# SKIP LOCKED: workers em paralelo pegam pedidos diferentes, sem esperar um ao outro.
CLAIM = sa.text(
    "UPDATE triages SET status = :running, started_at = :now"
    " WHERE incident_id = (SELECT incident_id FROM triages"
    " WHERE status = :pending ORDER BY requested_at, incident_id"
    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING incident_id, requested_at, started_at"
)


def _job(row: sa.Row[Any]) -> TriageJob:
    return TriageJob(
        incident_id=row.incident_id, requested_at=row.requested_at, started_at=row.started_at
    )


class PostgresTriageQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> TriageJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM, {"running": RUNNING, "pending": PENDING, "now": now}
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[TriageJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, requested_at, started_at FROM triages"
                    " WHERE status = :running AND started_at < :before"
                    " ORDER BY started_at, incident_id"
                ),
                {"running": RUNNING, "before": started_before},
            )
            return [_job(row) for row in result]

    async def open_since(self, since: datetime) -> list[Incident]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                    " firing, commander, handling_at, mitigated_at, resolved_at"
                    " FROM incidents WHERE state = ANY(:states) AND last_alert_at >= :since"
                    " ORDER BY last_alert_at DESC, id"
                ),
                {"states": OPEN_STATE_VALUES, "since": since},
            )
            return [incident_from_row(row) for row in result]

    async def finish(  # noqa: PLR0913 — mesmo port
        self,
        job: TriageJob,
        incident: Incident | None,
        *,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE triages SET status = :status, finished_at = :at, reason = :reason"
                    " WHERE incident_id = :incident_id AND status = :running"
                ),
                {
                    "incident_id": job.incident_id,
                    "running": RUNNING,
                    "status": DONE if reason is None else FAILED,
                    "reason": reason,
                    "at": at,
                },
            )
            if updated.rowcount == 0:
                # Já encerrada (ex.: o reaper fechou como falha): uma sugestão só por triagem.
                return
            uow = PostgresUnitOfWork(connection)
            if incident is not None:
                await uow.save(incident)
            await uow.append(events)
            for notification in notifications:
                await uow.enqueue(notification, at)
            await record_usages(connection, job.incident_id, usages, at)
