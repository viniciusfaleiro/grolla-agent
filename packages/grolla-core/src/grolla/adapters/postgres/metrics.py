"""Fatos das métricas do agente no PostgreSQL: uma consulta por parte, sem tabela própria.

Resolvidos vêm da projeção `incidents`, aprovados de `post_mortems` (com a avaliação) e as
respostas do "já vi isso" do log de eventos, só a última de cada incidente.
"""

from datetime import datetime

import sqlalchemy as sa

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.post_mortem import evaluation_from
from grolla.domain.incidents import EventType, IncidentState
from grolla.domain.metrics import MetricsFacts
from grolla.domain.post_mortem import PostMortemStatus

RESOLVED = sa.text(
    "SELECT resolved_at FROM incidents WHERE state = :resolved AND resolved_at >= :since"
    " ORDER BY resolved_at"
)
APPROVED = sa.text(
    "SELECT status_at, evaluation FROM post_mortems"
    " WHERE status IN (:approved, :closed) AND status_at >= :since ORDER BY status_at"
)
# Vale a última resposta do incidente (a última no log); se ela é anterior a `since`, ele fica de
# fora, mesmo com respostas mais antigas no período.
ANSWERS = sa.text(
    "SELECT at, same FROM (SELECT DISTINCT ON (incident_id) at,"
    " CAST(data->>'same' AS boolean) AS same FROM incident_events WHERE type = :type"
    " ORDER BY incident_id, id DESC) AS last WHERE at >= :since ORDER BY at"
)


class PostgresMetricsReader:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def facts(self, since: datetime) -> MetricsFacts:
        async with self._engine.connect() as connection:
            resolved = await connection.execute(
                RESOLVED, {"resolved": IncidentState.RESOLVED.value, "since": since}
            )
            approved = await connection.execute(
                APPROVED,
                {
                    "approved": PostMortemStatus.APPROVED.value,
                    "closed": PostMortemStatus.CLOSED.value,
                    "since": since,
                },
            )
            answers = await connection.execute(
                ANSWERS, {"type": EventType.SEEN_BEFORE_ANSWERED.value, "since": since}
            )
            return MetricsFacts(
                resolved=tuple(row.resolved_at for row in resolved),
                approved=tuple(
                    (row.status_at, evaluation_from(row.evaluation)) for row in approved
                ),
                answers=tuple((row.at, row.same) for row in answers),
            )
