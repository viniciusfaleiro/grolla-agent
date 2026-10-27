"""Post mortem no PostgreSQL (migrações 0009 e 0010): documento, versões das seções e a fila de
rascunhos e regenerações.

O documento é uma linha com as seções, o plano de ação e a avaliação do agente em `jsonb` (datas
em ISO 8601, ids como texto; avaliação nula é a vazia); as versões são só inserção. A tabela
`post_mortem_jobs` é a fila (`pendente` → `em_andamento` → `feito`/`falho`), com o rascunho único
por incidente.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
from grolla.adapters.postgres.incidents import incident_from_row
from grolla.application.investigation import LlmUsage
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob, PostMortemSummary
from grolla.domain.post_mortem import (
    ActionItem,
    ActionKind,
    ActionStatus,
    AgentEvaluation,
    PostMortem,
    PostMortemStatus,
    Section,
    SectionKey,
    SectionVersion,
)

JOB_PENDING = "pendente"
JOB_RUNNING = "em_andamento"
JOB_DONE = "feito"
JOB_FAILED = "falho"

# SKIP LOCKED: workers em paralelo pegam pedidos diferentes, sem esperar um ao outro.
CLAIM_JOB = sa.text(
    "UPDATE post_mortem_jobs SET status = :running, started_at = :now"
    " WHERE id = (SELECT id FROM post_mortem_jobs WHERE status = :pending"
    " ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING id, incident_id, section, instruction, author, started_at"
)
STUCK_JOBS = sa.text(
    "SELECT id, incident_id, section, instruction, author, started_at FROM post_mortem_jobs"
    " WHERE status = :running AND started_at < :before ORDER BY started_at, id"
)
UPSERT_POST_MORTEM = sa.text(
    "INSERT INTO post_mortems (incident_id, status, sections, actions, evaluation, status_by,"
    " status_at, created_at, updated_at) VALUES (:incident_id, :status,"
    " CAST(:sections AS jsonb), CAST(:actions AS jsonb), CAST(:evaluation AS jsonb), :status_by,"
    " :status_at, :created_at, :updated_at)"
    " ON CONFLICT (incident_id) DO UPDATE SET status = excluded.status,"
    " sections = excluded.sections, actions = excluded.actions,"
    " evaluation = excluded.evaluation,"
    " status_by = excluded.status_by, status_at = excluded.status_at,"
    " updated_at = excluded.updated_at"
)
INSERT_VERSION = sa.text(
    "INSERT INTO post_mortem_versions (incident_id, section, at, author, reason, body)"
    " VALUES (:incident_id, :section, :at, :author, :reason, :body)"
)
# Filtro ausente (NULL) não restringe; o período é o dia da detecção em UTC, inclusive.
SUMMARIES = sa.text(
    "SELECT i.id, i.title, i.state, i.severity, i.services, i.detected_at, i.last_alert_at,"
    " i.firing, i.commander, i.handling_at, i.mitigated_at, i.resolved_at,"
    " p.status AS pm_status, p.updated_at AS pm_updated_at,"
    " (SELECT count(*) FROM jsonb_array_elements(p.actions) AS a"
    "  WHERE a->>'status' = :open AND a->>'due' IS NOT NULL"
    "  AND CAST(a->>'due' AS date) < :today) AS overdue"
    " FROM post_mortems AS p JOIN incidents AS i ON i.id = p.incident_id"
    " WHERE (CAST(:status AS text) IS NULL OR p.status = :status)"
    " AND (CAST(:severity AS text) IS NULL OR i.severity = :severity)"
    " AND (CAST(:service AS text) IS NULL OR :service = ANY(i.services))"
    " AND (CAST(:since AS date) IS NULL"
    "  OR CAST(i.detected_at AT TIME ZONE 'UTC' AS date) >= :since)"
    " AND (CAST(:until AS date) IS NULL"
    "  OR CAST(i.detected_at AT TIME ZONE 'UTC' AS date) <= :until)"
    " ORDER BY i.detected_at DESC, i.id LIMIT :limit"
)


def _sections_json(sections: Sequence[Section]) -> str:
    return json.dumps(
        [
            {
                "key": s.key.value,
                "body": s.body,
                "author": s.author,
                "updated_at": s.updated_at.isoformat(),
            }
            for s in sections
        ]
    )


def _actions_json(actions: Sequence[ActionItem]) -> str:
    return json.dumps(
        [
            {
                "id": str(a.id),
                "description": a.description,
                "kind": a.kind.value,
                "owner": a.owner,
                "due": None if a.due is None else a.due.isoformat(),
                "status": a.status.value,
            }
            for a in actions
        ]
    )


def _evaluation_json(evaluation: AgentEvaluation) -> str:
    return json.dumps(
        {
            "main_hypothesis_correct": evaluation.main_hypothesis_correct,
            "minutes_to_right": evaluation.minutes_to_right,
            "memory_helped": evaluation.memory_helped,
            "author": evaluation.author,
            "at": None if evaluation.at is None else evaluation.at.isoformat(),
        }
    )


def evaluation_from(data: Mapping[str, Any] | None) -> AgentEvaluation:
    if data is None:
        return AgentEvaluation()
    return AgentEvaluation(
        main_hypothesis_correct=data["main_hypothesis_correct"],
        minutes_to_right=data["minutes_to_right"],
        memory_helped=data["memory_helped"],
        author=data["author"],
        at=None if data["at"] is None else datetime.fromisoformat(data["at"]),
    )


def _section(data: Mapping[str, Any]) -> Section:
    return Section(
        key=SectionKey(data["key"]),
        body=data["body"],
        author=data["author"],
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


def _action(data: Mapping[str, Any]) -> ActionItem:
    return ActionItem(
        id=UUID(data["id"]),
        description=data["description"],
        kind=ActionKind(data["kind"]),
        owner=data["owner"],
        due=None if data["due"] is None else date.fromisoformat(data["due"]),
        status=ActionStatus(data["status"]),
    )


def _post_mortem(row: sa.Row[Any]) -> PostMortem:
    return PostMortem(
        incident_id=row.incident_id,
        status=PostMortemStatus(row.status),
        sections=tuple(_section(s) for s in row.sections),
        actions=tuple(_action(a) for a in row.actions),
        evaluation=evaluation_from(row.evaluation),
        created_at=row.created_at,
        updated_at=row.updated_at,
        status_by=row.status_by,
        status_at=row.status_at,
    )


def _job(row: sa.Row[Any]) -> PostMortemJob:
    return PostMortemJob(
        id=row.id,
        incident_id=row.incident_id,
        key=None if row.section is None else SectionKey(row.section),
        instruction=row.instruction,
        author=row.author,
        started_at=row.started_at,
    )


async def _save(
    connection: AsyncConnection, pm: PostMortem, versions: Sequence[SectionVersion]
) -> None:
    await connection.execute(
        UPSERT_POST_MORTEM,
        {
            "incident_id": pm.incident_id,
            "status": pm.status.value,
            "sections": _sections_json(pm.sections),
            "actions": _actions_json(pm.actions),
            "evaluation": _evaluation_json(pm.evaluation),
            "status_by": pm.status_by,
            "status_at": pm.status_at,
            "created_at": pm.created_at,
            "updated_at": pm.updated_at,
        },
    )
    if versions:
        await connection.execute(
            INSERT_VERSION,
            [
                {
                    "incident_id": v.incident_id,
                    "section": v.key.value,
                    "at": v.at,
                    "author": v.author,
                    "reason": v.reason,
                    "body": v.body,
                }
                for v in versions
            ],
        )


class PostgresPostMortemStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def get(self, incident_id: UUID) -> PostMortem | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, status, sections, actions, evaluation, status_by,"
                    " status_at, created_at, updated_at FROM post_mortems WHERE incident_id = :id"
                ),
                {"id": incident_id},
            )
            row = result.one_or_none()
            return None if row is None else _post_mortem(row)

    async def save(self, pm: PostMortem, versions: Sequence[SectionVersion]) -> None:
        async with self._engine.begin() as connection:
            await _save(connection, pm, versions)

    async def versions(self, incident_id: UUID, key: SectionKey) -> list[SectionVersion]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT at, author, reason, body FROM post_mortem_versions"
                    " WHERE incident_id = :id AND section = :section ORDER BY at DESC, id DESC"
                ),
                {"id": incident_id, "section": key.value},
            )
            return [
                SectionVersion(
                    incident_id=incident_id,
                    key=key,
                    at=row.at,
                    author=row.author,
                    reason=row.reason,
                    body=row.body,
                )
                for row in result
            ]

    async def summaries(self, filters: PostMortemFilters, today: date) -> list[PostMortemSummary]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                SUMMARIES,
                {
                    "open": ActionStatus.OPEN.value,
                    "today": today,
                    "status": None if filters.status is None else filters.status.value,
                    "severity": None if filters.severity is None else filters.severity.value,
                    "service": filters.service,
                    "since": filters.since,
                    "until": filters.until,
                    "limit": filters.limit,
                },
            )
            return [
                PostMortemSummary(
                    incident=incident_from_row(row),
                    status=PostMortemStatus(row.pm_status),
                    updated_at=row.pm_updated_at,
                    overdue=row.overdue,
                )
                for row in result
            ]

    async def request_regeneration(
        self,
        incident_id: UUID,
        key: SectionKey,
        *,
        instruction: str | None,
        author: str,
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO post_mortem_jobs"
                    " (incident_id, section, instruction, author, status, requested_at)"
                    " VALUES (:incident_id, :section, :instruction, :author, :pending, :at)"
                ),
                {
                    "incident_id": incident_id,
                    "section": key.value,
                    "instruction": instruction,
                    "author": author,
                    "pending": JOB_PENDING,
                    "at": at,
                },
            )

    async def claim(self, now: datetime) -> PostMortemJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_JOB, {"running": JOB_RUNNING, "pending": JOB_PENDING, "now": now}
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[PostMortemJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                STUCK_JOBS, {"running": JOB_RUNNING, "before": started_before}
            )
            return [_job(row) for row in result]

    async def finish(  # noqa: PLR0913 — mesmo port
        self,
        job: PostMortemJob,
        pm: PostMortem | None,
        *,
        versions: Sequence[SectionVersion],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE post_mortem_jobs SET status = :status, finished_at = :at,"
                    " reason = :reason WHERE id = :id AND status = :running"
                ),
                {
                    "id": job.id,
                    "running": JOB_RUNNING,
                    "status": JOB_DONE if reason is None else JOB_FAILED,
                    "reason": reason,
                    "at": at,
                },
            )
            if updated.rowcount == 0:
                # Já encerrado (ex.: o reaper fechou como falho): o resultado tardio se descarta.
                return
            if pm is not None:
                await _save(connection, pm, versions)
            await record_usages(connection, job.incident_id, usages, at)
