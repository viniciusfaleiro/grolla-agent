import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from grolla.application.clock import Clock
from grolla.application.incidents import (
    AlertIngress,
    IncidentRepository,
    IntakePolicy,
    InvalidAlertPayloadError,
    receive_alerts,
)
from grolla.domain.analysis import PARTIAL_REASON_TEXT, AnalysisStatus
from grolla.domain.incidents import EventType

HealthCheck = Callable[[], Awaitable[bool]]
LatestHeartbeat = Callable[[], Awaitable[datetime | None]]

WORKER_HEARTBEAT_LIMIT = timedelta(minutes=2)
INCIDENT_LIST_LIMIT = 200

EVENT_LABELS = {
    EventType.ALERT_RECEIVED: "Alerta recebido",
    EventType.ALERT_GROUPED: "Alerta agrupado",
    EventType.ALERT_RESOLVED: "Alerta resolvido",
    EventType.SEVERITY_CHANGED: "Severidade alterada",
    EventType.STATE_CHANGED: "Estado alterado",
    EventType.INCIDENT_NORMALIZED: "Incidente normalizado",
    EventType.NOTIFICATION_QUEUED: "Notificação enfileirada",
    EventType.NOTIFICATION_DELIVERED: "Notificação entregue",
    EventType.NOTIFICATION_FAILED: "Notificação falhou",
    EventType.ANALYSIS_REQUESTED: "Análise pedida",
    EventType.ANALYSIS_COMPLETED: "Análise concluída",
    EventType.ANALYSIS_PARTIAL: "Análise parcial",
}

ANALYSIS_STATUS_LABELS = {
    AnalysisStatus.PENDING: "análise em andamento",
    AnalysisStatus.RUNNING: "análise em andamento",
    AnalysisStatus.COMPLETED: "análise concluída",
    AnalysisStatus.PARTIAL: "análise parcial",
}

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@dataclass(frozen=True)
class Services:
    """O que as rotas usam; nos testes, montado com os fakes dos ports."""

    database_is_up: HealthCheck
    latest_heartbeat: LatestHeartbeat
    ingress: AlertIngress
    repository: IncidentRepository
    intake: IntakePolicy
    clock: Clock


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="agent-grolla")

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if await services.database_is_up():
            return {"status": "ok"}
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}

    @app.get("/health/worker")
    async def worker_health() -> JSONResponse:  # pyright: ignore[reportUnusedFunction]
        last = await services.latest_heartbeat()
        alive = last is not None and services.clock.now() - last <= WORKER_HEARTBEAT_LIMIT
        return JSONResponse(
            {"last_heartbeat": last.isoformat() if last else None},
            status_code=status.HTTP_200_OK if alive else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.post("/webhooks/alerts", status_code=status.HTTP_202_ACCEPTED)
    async def alerts_webhook(request: Request) -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        if not services.ingress.authenticate(request.headers):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
        try:
            payload: Any = json.loads(await request.body())
            alerts = services.ingress.parse(payload)
        except (ValueError, InvalidAlertPayloadError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        await receive_alerts(alerts, services.repository, services.intake, services.clock)
        return {"status": "accepted"}

    @app.get("/incidents", response_class=HTMLResponse)
    async def incidents(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await services.repository.list_incidents(INCIDENT_LIST_LIMIT)
        return templates.TemplateResponse(request, "incidents.html", {"incidents": found})

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    async def incident(request: Request, incident_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        try:
            found = await services.repository.get_incident(UUID(incident_id))
        except ValueError:
            found = None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado")
        context = {
            "incident": found,
            "alerts": await services.repository.alerts_of(found.id),
            "events": await services.repository.events_of(found.id),
            "analysis": await services.repository.analysis_of(found.id),
            "event_labels": EVENT_LABELS,
            "analysis_labels": ANALYSIS_STATUS_LABELS,
            "partial_reasons": PARTIAL_REASON_TEXT,  # mesmo texto do card
        }
        return templates.TemplateResponse(request, "incident.html", context)

    return app
