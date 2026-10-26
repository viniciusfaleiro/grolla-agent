"""Log de eventos, projeções (incidentes, análises, validações), alertas e outbox (0002 a 0004)."""

import json
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.incidents import IncidentUnitOfWork, OutboxItem
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    PartialReason,
    Verdict,
    Verification,
)
from grolla.domain.chat import ChatMessage, ReplyStatus
from grolla.domain.incidents import (
    OPEN_STATES,
    Alert,
    AlertStatus,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Notification,
    NotificationKind,
    Severity,
)

SYSTEM_AUTHOR = "sistema"
# Primeira chave dos advisory locks por serviço (a segunda é o hash do nome do serviço).
SERVICE_LOCK_NAMESPACE = 7042
OPEN_STATE_VALUES = sorted(state.value for state in OPEN_STATES)


def _incident(row: sa.Row[Any]) -> Incident:
    return Incident(
        id=row.id,
        title=row.title,
        state=IncidentState(row.state),
        severity=Severity(row.severity),
        services=tuple(row.services),
        detected_at=row.detected_at,
        last_alert_at=row.last_alert_at,
        firing=frozenset(row.firing),
        commander=row.commander,
        handling_at=row.handling_at,
        mitigated_at=row.mitigated_at,
        resolved_at=row.resolved_at,
    )


def _chat_message(row: sa.Row[Any]) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        incident_id=row.incident_id,
        author=row.author,
        text=row.text,
        at=row.at,
        evaluations=_evaluations(row.evaluations),
        reply_status=None if row.reply_status is None else ReplyStatus(row.reply_status),
        reply_to=row.reply_to,
    )


def _alert(row: sa.Row[Any]) -> Alert:
    return Alert(
        fingerprint=row.fingerprint,
        status=AlertStatus(row.status),
        rule_name=row.rule_name,
        service=row.service,
        labels=row.labels,
        summary=row.summary,
        description=row.description,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        source_url=row.source_url,
        raw=row.raw,
    )


def _json_default(value: object) -> str:
    """Horários e ids que o domínio ponha em `data` viram texto (ISO 8601 e UUID canônico)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"valor sem representação JSON: {type(value).__name__}")


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, default=_json_default)


def evaluations_data(evaluations: Sequence[Evaluation]) -> list[dict[str, Any]]:
    return [
        {"tool": e.tool, "subject": e.subject, "finding": e.finding, "url": e.url, "ok": e.ok}
        for e in evaluations
    ]


def hypotheses_data(hypotheses: Sequence[Hypothesis]) -> list[dict[str, Any]]:
    return [
        {
            "title": h.title,
            "confidence": h.confidence,
            "evidence": _evidence_data(h.evidence),
        }
        for h in hypotheses
    ]


def _evidence_data(evidence: Sequence[Evidence]) -> list[dict[str, Any]]:
    return [{"text": e.text, "url": e.url} for e in evidence]


def _evidence(data: Sequence[Mapping[str, Any]]) -> tuple[Evidence, ...]:
    return tuple(Evidence(text=e["text"], url=e["url"]) for e in data)


def verdicts_data(verdicts: Sequence[HypothesisVerdict]) -> list[dict[str, Any]]:
    return [
        {
            "hypothesis": v.hypothesis,
            "verdict": v.verdict.value,
            "reasoning": v.reasoning,
            "evidence": _evidence_data(v.evidence),
        }
        for v in verdicts
    ]


def actions_data(actions: Sequence[Action]) -> list[dict[str, Any]]:
    return [
        {"action": a.action, "target": a.target, "expected": a.expected, "link": a.link}
        for a in actions
    ]


def _verification_data(verification: Verification) -> dict[str, Any]:
    return {
        "incident_id": str(verification.incident_id),
        "status": verification.status.value,
        "requested_at": verification.requested_at,
        "started_at": verification.started_at,
        "finished_at": verification.finished_at,
        "verdicts": verdicts_data(verification.verdicts),
        "root_cause": verification.root_cause,
        "actions": actions_data(verification.actions),
        "missing": verification.missing,
        "evaluations": evaluations_data(verification.evaluations),
        "partial_reason": None
        if verification.partial_reason is None
        else verification.partial_reason.value,
    }


def _verification(data: Mapping[str, Any]) -> Verification:
    """Lê a validação do JSON da linha de `verifications` ou do card na outbox."""
    reason = data["partial_reason"]
    return Verification(
        incident_id=UUID(data["incident_id"]),
        status=AnalysisStatus(data["status"]),
        requested_at=datetime.fromisoformat(data["requested_at"]),
        started_at=_optional_datetime(data["started_at"]),
        finished_at=_optional_datetime(data["finished_at"]),
        verdicts=tuple(
            HypothesisVerdict(
                hypothesis=v["hypothesis"],
                verdict=Verdict(v["verdict"]),
                reasoning=v["reasoning"],
                evidence=_evidence(v["evidence"]),
            )
            for v in data["verdicts"]
        ),
        root_cause=data["root_cause"],
        actions=tuple(
            Action(action=a["action"], target=a["target"], expected=a["expected"], link=a["link"])
            for a in data["actions"]
        ),
        missing=data["missing"],
        evaluations=_evaluations(data["evaluations"]),
        partial_reason=None if reason is None else PartialReason(reason),
    )


def _evaluations(data: Sequence[Mapping[str, Any]]) -> tuple[Evaluation, ...]:
    return tuple(
        Evaluation(
            tool=e["tool"], subject=e["subject"], finding=e["finding"], url=e["url"], ok=e["ok"]
        )
        for e in data
    )


def _hypotheses(data: Sequence[Mapping[str, Any]]) -> tuple[Hypothesis, ...]:
    return tuple(
        Hypothesis(
            title=h["title"],
            confidence=h["confidence"],
            evidence=_evidence(h["evidence"]),
        )
        for h in data
    )


def _analysis_data(analysis: Analysis) -> dict[str, Any]:
    return {
        "incident_id": str(analysis.incident_id),
        "status": analysis.status.value,
        "requested_at": analysis.requested_at,
        "started_at": analysis.started_at,
        "finished_at": analysis.finished_at,
        "summary": analysis.summary,
        "evaluations": evaluations_data(analysis.evaluations),
        "hypotheses": hypotheses_data(analysis.hypotheses),
        "plan": list(analysis.plan),
        "partial_reason": None
        if analysis.partial_reason is None
        else analysis.partial_reason.value,
    }


def _analysis(data: Mapping[str, Any]) -> Analysis:
    """Lê a análise do JSON da linha de `analyses` ou do card na outbox."""
    reason = data["partial_reason"]
    return Analysis(
        incident_id=UUID(data["incident_id"]),
        status=AnalysisStatus(data["status"]),
        requested_at=datetime.fromisoformat(data["requested_at"]),
        started_at=_optional_datetime(data["started_at"]),
        finished_at=_optional_datetime(data["finished_at"]),
        summary=data["summary"],
        evaluations=_evaluations(data["evaluations"]),
        hypotheses=_hypotheses(data["hypotheses"]),
        plan=tuple(data["plan"]),
        partial_reason=None if reason is None else PartialReason(reason),
    )


def _optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _notification_json(notification: Notification) -> str:
    analysis = notification.analysis
    verification = notification.verification
    return _json(
        {
            "kind": notification.kind.value,
            "incident_id": str(notification.incident_id),
            "title": notification.title,
            "severity": notification.severity.value,
            "services": list(notification.services),
            "summary": notification.summary,
            "evidence": notification.evidence,
            "source_url": notification.source_url,
            "page_url": notification.page_url,
            "analysis": None if analysis is None else _analysis_data(analysis),
            "verification": None if verification is None else _verification_data(verification),
        }
    )


def _notification(data: Mapping[str, Any]) -> Notification:
    analysis = data.get("analysis")  # cards gravados antes da 0003 não têm a chave
    verification = data.get("verification")  # nem antes da 0004
    return Notification(
        kind=NotificationKind(data["kind"]),
        incident_id=UUID(data["incident_id"]),
        title=data["title"],
        severity=Severity(data["severity"]),
        services=tuple(data["services"]),
        summary=data["summary"],
        evidence=data["evidence"],
        source_url=data["source_url"],
        page_url=data["page_url"],
        analysis=None if analysis is None else _analysis(analysis),
        verification=None if verification is None else _verification(verification),
    )


async def insert_events(connection: AsyncConnection, events: Sequence[IncidentEvent]) -> None:
    if not events:
        return
    await connection.execute(
        sa.text(
            "INSERT INTO incident_events (incident_id, at, type, author, data)"
            " VALUES (:incident_id, :at, :type, :author, CAST(:data AS jsonb))"
        ),
        [
            {
                "incident_id": event.incident_id,
                "at": event.at,
                "type": event.type.value,
                "author": event.author,
                "data": _json(event.data),
            }
            for event in events
        ],
    )


async def insert_message(
    connection: AsyncConnection,
    message: ChatMessage,
    reply_status: ReplyStatus | None,
    reply_to: int | None = None,
) -> ChatMessage:
    """Grava a mensagem (o `id` dela é ignorado) e a devolve com o id gerado pelo banco."""
    result = await connection.execute(
        sa.text(
            "INSERT INTO chat_messages (incident_id, author, text, at, evaluations,"
            " reply_status, reply_to) VALUES (:incident_id, :author, :text, :at,"
            " CAST(:evaluations AS jsonb), :reply_status, :reply_to)"
            " RETURNING id, incident_id, author, text, at, evaluations, reply_status, reply_to"
        ),
        {
            "incident_id": message.incident_id,
            "author": message.author,
            "text": message.text,
            "at": message.at,
            "evaluations": json.dumps(evaluations_data(message.evaluations)),
            "reply_status": None if reply_status is None else reply_status.value,
            "reply_to": reply_to,
        },
    )
    return _chat_message(result.one())


class PostgresUnitOfWork:
    """Escritas de uma transação; quem abre e fecha a transação é o repositório."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def register_alert(self, alert: Alert) -> bool:
        result = await self._connection.execute(
            sa.text(
                "INSERT INTO alerts ("
                "fingerprint, status, rule_name, service, labels, summary, description,"
                " starts_at, ends_at, source_url, raw"
                ") VALUES (:fingerprint, :status, :rule_name,"
                " :service, CAST(:labels AS jsonb), :summary, :description, :starts_at, :ends_at,"
                " :source_url, CAST(:raw AS jsonb))"
                " ON CONFLICT (fingerprint, starts_at, status) DO NOTHING RETURNING id"
            ),
            {
                "fingerprint": alert.fingerprint,
                "status": alert.status.value,
                "rule_name": alert.rule_name,
                "service": alert.service,
                "labels": _json(alert.labels),
                "summary": alert.summary,
                "description": alert.description,
                "starts_at": alert.starts_at,
                "ends_at": alert.ends_at,
                "source_url": alert.source_url,
                "raw": _json(alert.raw),
            },
        )
        return result.scalar_one_or_none() is not None

    async def active_incidents(self, service: str, since: datetime) -> list[Incident]:
        # Serializa até o commit as transações do mesmo serviço: dois webhooks simultâneos
        # não podem abrir dois incidentes para o mesmo problema.
        await self._connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:service))"),
            {"namespace": SERVICE_LOCK_NAMESPACE, "service": service},
        )
        result = await self._connection.execute(
            sa.text(
                "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                " firing, commander, handling_at, mitigated_at, resolved_at"
                " FROM incidents"
                " WHERE :service = ANY (services) AND state = ANY (:open_states)"
                " AND (cardinality(firing) > 0 OR last_alert_at >= :since)"
                " ORDER BY last_alert_at DESC FOR UPDATE"
            ),
            {"service": service, "since": since, "open_states": OPEN_STATE_VALUES},
        )
        return [_incident(row) for row in result]

    async def incident_firing(self, fingerprint: str) -> Incident | None:
        # FOR UPDATE aqui e em active_incidents: resolução e agrupamento simultâneos no mesmo
        # incidente não perdem a atualização de `firing` um do outro.
        result = await self._connection.execute(
            sa.text(
                "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                " firing, commander, handling_at, mitigated_at, resolved_at"
                " FROM incidents WHERE :fingerprint = ANY (firing)"
                " ORDER BY last_alert_at DESC LIMIT 1 FOR UPDATE"
            ),
            {"fingerprint": fingerprint},
        )
        row = result.one_or_none()
        return None if row is None else _incident(row)

    async def save(self, incident: Incident) -> None:
        await self._connection.execute(
            sa.text(
                "INSERT INTO incidents (id, title, state, severity, services, detected_at,"
                " last_alert_at, firing, commander, handling_at, mitigated_at, resolved_at)"
                " VALUES (:id, :title, :state,"
                " :severity, CAST(:services AS text[]), :detected_at, :last_alert_at,"
                " CAST(:firing AS text[]), :commander, :handling_at, :mitigated_at, :resolved_at)"
                " ON CONFLICT (id) DO UPDATE SET title = excluded.title,"
                " state = excluded.state, severity = excluded.severity,"
                " services = excluded.services, detected_at = excluded.detected_at,"
                " last_alert_at = excluded.last_alert_at, firing = excluded.firing,"
                " commander = excluded.commander, handling_at = excluded.handling_at,"
                " mitigated_at = excluded.mitigated_at, resolved_at = excluded.resolved_at"
            ),
            {
                "id": incident.id,
                "title": incident.title,
                "state": incident.state.value,
                "severity": incident.severity.value,
                "services": list(incident.services),
                "detected_at": incident.detected_at,
                "last_alert_at": incident.last_alert_at,
                "firing": sorted(incident.firing),
                "commander": incident.commander,
                "handling_at": incident.handling_at,
                "mitigated_at": incident.mitigated_at,
                "resolved_at": incident.resolved_at,
            },
        )

    async def append(self, events: Sequence[IncidentEvent]) -> None:
        await insert_events(self._connection, events)

    async def link_alert(self, alert: Alert, incident_id: UUID) -> None:
        await self._connection.execute(
            sa.text(
                "UPDATE alerts SET incident_id = :incident_id WHERE fingerprint = :fingerprint"
                " AND starts_at = :starts_at AND status = :status"
            ),
            {
                "incident_id": incident_id,
                "fingerprint": alert.fingerprint,
                "starts_at": alert.starts_at,
                "status": alert.status.value,
            },
        )

    async def incident_for_update(self, incident_id: UUID) -> Incident | None:
        # Duas pessoas agindo ao mesmo tempo no war room: a segunda espera o commit da primeira
        # e decide sobre o estado já atualizado.
        result = await self._connection.execute(
            sa.text(
                "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                " firing, commander, handling_at, mitigated_at, resolved_at"
                " FROM incidents WHERE id = :id FOR UPDATE"
            ),
            {"id": incident_id},
        )
        row = result.one_or_none()
        return None if row is None else _incident(row)

    async def add_message(self, message: ChatMessage, wants_reply: bool) -> ChatMessage:
        return await insert_message(
            self._connection,
            message,
            reply_status=ReplyStatus.PENDING if wants_reply else None,
        )

    async def request_analysis(self, incident_id: UUID, at: datetime) -> None:
        await self._connection.execute(
            sa.text(
                "INSERT INTO analyses (incident_id, status, requested_at)"
                # Pedido repetido mantém o primeiro: um erro abortaria a transação do alerta.
                " VALUES (:incident_id, :status, :at) ON CONFLICT (incident_id) DO NOTHING"
            ),
            {"incident_id": incident_id, "status": AnalysisStatus.PENDING.value, "at": at},
        )

    async def enqueue(self, notification: Notification, at: datetime) -> None:
        await self._connection.execute(
            sa.text(
                "INSERT INTO outbox (incident_id, notification, created_at, next_attempt_at)"
                " VALUES (:incident_id, CAST(:notification AS jsonb), :at, :at)"
            ),
            {
                "incident_id": notification.incident_id,
                "notification": _notification_json(notification),
                "at": at,
            },
        )


class PostgresIncidentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[IncidentUnitOfWork]:
        """Uma transação: commit ao sair sem erro, rollback em exceção."""
        async with self._engine.begin() as connection:
            yield PostgresUnitOfWork(connection)

    async def list_incidents(self, limit: int) -> list[Incident]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                    " firing, commander, handling_at, mitigated_at, resolved_at"
                    " FROM incidents"
                    " ORDER BY detected_at DESC, id LIMIT :limit"
                ),
                {"limit": limit},
            )
            return [_incident(row) for row in result]

    async def get_incident(self, incident_id: UUID) -> Incident | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, title, state, severity, services, detected_at, last_alert_at,"
                    " firing, commander, handling_at, mitigated_at, resolved_at"
                    " FROM incidents WHERE id = :id"
                ),
                {"id": incident_id},
            )
            row = result.one_or_none()
            return None if row is None else _incident(row)

    async def alerts_of(self, incident_id: UUID) -> list[Alert]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT "
                    "fingerprint, status, rule_name, service, labels, summary, description,"
                    " starts_at, ends_at, source_url, raw"
                    " FROM alerts WHERE incident_id = :id ORDER BY id"
                ),
                {"id": incident_id},
            )
            return [_alert(row) for row in result]

    async def chat_of(self, incident_id: UUID) -> list[ChatMessage]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, incident_id, author, text, at, evaluations, reply_status, reply_to"
                    " FROM chat_messages"
                    " WHERE incident_id = :id ORDER BY at, id"
                ),
                {"id": incident_id},
            )
            return [_chat_message(row) for row in result]

    async def verification_of(self, incident_id: UUID) -> Verification | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                # Como JSON, igual a `analysis_of`: o mesmo formato do card na outbox.
                sa.text(
                    "SELECT to_jsonb(verifications) FROM verifications WHERE incident_id = :id"
                ),
                {"id": incident_id},
            )
            data = result.scalar_one_or_none()
            return None if data is None else _verification(data)

    async def analysis_of(self, incident_id: UUID) -> Analysis | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                # Como JSON: a linha chega no mesmo formato do card de análise na outbox.
                sa.text("SELECT to_jsonb(analyses) FROM analyses WHERE incident_id = :id"),
                {"id": incident_id},
            )
            data = result.scalar_one_or_none()
            return None if data is None else _analysis(data)

    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, at, type, author, data FROM incident_events"
                    " WHERE incident_id = :id ORDER BY at, id"
                ),
                {"id": incident_id},
            )
            return [
                IncidentEvent(
                    incident_id=row.incident_id,
                    at=row.at,
                    type=EventType(row.type),
                    data=row.data,
                    author=row.author,
                )
                for row in result
            ]


class PostgresNotificationOutbox:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT id, notification, attempts FROM outbox AS item"
                    " WHERE delivered_at IS NULL AND failed_at IS NULL"
                    " AND next_attempt_at <= :now"
                    # Card mais antigo do incidente esperando retentativa segura os seguintes.
                    " AND NOT EXISTS (SELECT 1 FROM outbox AS older"
                    " WHERE older.incident_id = item.incident_id AND older.id < item.id"
                    " AND older.delivered_at IS NULL AND older.failed_at IS NULL"
                    " AND older.next_attempt_at > :now)"
                    " ORDER BY id LIMIT :limit"
                ),
                {"now": now, "limit": limit},
            )
            return [
                OutboxItem(
                    id=row.id, notification=_notification(row.notification), attempts=row.attempts
                )
                for row in result
            ]

    async def delivered(self, item: OutboxItem, at: datetime) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET delivered_at = :at, attempts = attempts + 1 WHERE id = :id"
                ),
                {"id": item.id, "at": at},
            )
            await insert_events(
                connection,
                [_outcome(item, at, EventType.NOTIFICATION_DELIVERED, {})],
            )

    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET next_attempt_at = :at, attempts = attempts + 1,"
                    " last_error = :error WHERE id = :id"
                ),
                {"id": item.id, "at": at, "error": error},
            )

    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE outbox SET failed_at = :at, attempts = attempts + 1,"
                    " last_error = :error WHERE id = :id"
                ),
                {"id": item.id, "at": at, "error": error},
            )
            await insert_events(
                connection,
                [_outcome(item, at, EventType.NOTIFICATION_FAILED, {"error": error})],
            )


def _outcome(
    item: OutboxItem, at: datetime, type_: EventType, extra: Mapping[str, Any]
) -> IncidentEvent:
    return IncidentEvent(
        incident_id=item.notification.incident_id,
        at=at,
        type=type_,
        data={"kind": item.notification.kind.value, "attempts": item.attempts + 1, **extra},
        author=SYSTEM_AUTHOR,
    )
