"""Ciclos de acompanhamento no PostgreSQL (migração 0008).

A tabela `cycles` é ao mesmo tempo a fila (status `pendente`/`em_andamento`) e a projeção do
resultado, como `analyses`. `follow_ups` guarda o pedido de ciclo antecipado do incidente.
"""

import json
from collections import defaultdict
from collections.abc import Collection, Sequence
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import (
    OPEN_STATE_VALUES,
    PostgresUnitOfWork,
    cycle_data,
    incident_from_row,
    read_cycle,
)
from grolla.application.follow_up import CycleCandidate
from grolla.application.investigation import STAGE_FOLLOW_UP, LlmUsage
from grolla.domain.follow_up import Cycle, CycleStatus
from grolla.domain.incidents import IncidentEvent, Notification, Severity

CANDIDATES = sa.text(
    "SELECT incidents.id, title, state, severity, services, detected_at, last_alert_at, firing,"
    " commander, handling_at, mitigated_at, resolved_at,"
    " analyses.finished_at AS analysis_done_at,"
    " follow_ups.requested_at AS early_requested_at,"
    # Todos os tokens processados, como conta o TokenBudget.
    " (SELECT coalesce(sum(input_tokens + output_tokens + cache_read_tokens"
    " + cache_creation_tokens), 0) FROM llm_usage"
    " WHERE llm_usage.incident_id = incidents.id AND stage = :stage) AS tokens"
    " FROM incidents"
    " LEFT JOIN analyses ON analyses.incident_id = incidents.id"
    " LEFT JOIN follow_ups ON follow_ups.incident_id = incidents.id"
    " WHERE incidents.state = ANY (:open_states) AND incidents.severity = ANY (:severities)"
    " ORDER BY incidents.detected_at, incidents.id"
)
# SKIP LOCKED: workers em paralelo pegam ciclos diferentes, sem esperar um ao outro.
CLAIM_CYCLE = sa.text(
    "UPDATE cycles SET status = :running, started_at = :now"
    " WHERE (incident_id, number) = (SELECT incident_id, number FROM cycles"
    " WHERE status = :pending ORDER BY requested_at, incident_id, number"
    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING to_jsonb(cycles)"
)


class PostgresCycleQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def candidates(self, severities: Collection[Severity]) -> list[CycleCandidate]:
        async with self._engine.connect() as connection:
            rows = list(
                await connection.execute(
                    CANDIDATES,
                    {
                        "stage": STAGE_FOLLOW_UP,
                        "open_states": OPEN_STATE_VALUES,
                        "severities": sorted(severity.value for severity in severities),
                    },
                )
            )
            cycles: defaultdict[UUID, list[Cycle]] = defaultdict(list)
            result = await connection.execute(
                sa.text(
                    "SELECT to_jsonb(cycles) FROM cycles WHERE incident_id = ANY (:ids)"
                    " ORDER BY incident_id, number"
                ),
                {"ids": [row.id for row in rows]},
            )
            for data in result.scalars():
                cycle = read_cycle(data)
                cycles[cycle.incident_id].append(cycle)
        return [
            CycleCandidate(
                incident=incident_from_row(row),
                analysis_done_at=row.analysis_done_at,
                cycles=tuple(cycles[row.id]),
                requested_at=row.early_requested_at,
                tokens=int(row.tokens),
            )
            for row in rows
        ]

    async def enqueue(self, incident_id: UUID, number: int, at: datetime) -> bool:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                sa.text(
                    "INSERT INTO cycles (incident_id, number, status, requested_at)"
                    " VALUES (:incident_id, :number, :pending, :at)"
                    " ON CONFLICT (incident_id, number) DO NOTHING RETURNING number"
                ),
                {
                    "incident_id": incident_id,
                    "number": number,
                    "pending": CycleStatus.PENDING.value,
                    "at": at,
                },
            )
            return result.scalar_one_or_none() is not None

    async def claim(self, now: datetime) -> Cycle | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_CYCLE,
                {
                    "running": CycleStatus.RUNNING.value,
                    "pending": CycleStatus.PENDING.value,
                    "now": now,
                },
            )
            data = result.scalar_one_or_none()
            return None if data is None else read_cycle(data)

    async def stuck(self, started_before: datetime) -> list[Cycle]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT to_jsonb(cycles) FROM cycles"
                    " WHERE status = :running AND started_at < :before"
                    " ORDER BY started_at, incident_id, number"
                ),
                {"running": CycleStatus.RUNNING.value, "before": started_before},
            )
            return [read_cycle(data) for data in result.scalars()]

    async def finish(
        self,
        cycle: Cycle,
        events: Sequence[IncidentEvent],
        notifications: Sequence[Notification],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        data = cycle_data(cycle)
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE cycles SET status = :status, started_at = :started_at,"
                    " finished_at = :finished_at, trend = :trend, error_rate = :error_rate,"
                    " summary = :summary, changed = :changed,"
                    " evidence = CAST(:evidence AS jsonb),"
                    " evaluations = CAST(:evaluations AS jsonb),"
                    " partial_reason = :partial_reason, posted = :posted"
                    " WHERE incident_id = :incident_id AND number = :number"
                    " AND status = :running"
                ),
                {
                    **data,
                    "incident_id": cycle.incident_id,
                    "running": CycleStatus.RUNNING.value,
                    "evidence": json.dumps(data["evidence"]),
                    "evaluations": json.dumps(data["evaluations"]),
                },
            )
            if updated.rowcount == 0:
                # Já encerrado (ex.: o reaper fechou como interrompido): um desfecho só por ciclo.
                return
            uow = PostgresUnitOfWork(connection)
            await uow.append(events)
            for notification in notifications:
                await uow.enqueue(notification, at)
            await record_usages(connection, cycle.incident_id, usages, at)
            await connection.execute(
                sa.text("DELETE FROM follow_ups WHERE incident_id = :id"),
                {"id": cycle.incident_id},
            )
