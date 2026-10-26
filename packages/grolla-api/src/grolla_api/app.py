import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from grolla.application.clock import Clock
from grolla.application.incidents import (
    AlertIngress,
    IncidentRepository,
    IntakePolicy,
    InvalidAlertPayloadError,
    receive_alerts,
)
from grolla.application.war_room import IncidentNotFoundError, act, ask
from grolla.domain.analysis import PARTIAL_REASON_TEXT, AnalysisStatus
from grolla.domain.incidents import EventType, Incident
from grolla.domain.war_room import InvalidActionError

from grolla_api.war_room import (
    AUTHOR_COOKIE,
    AUTHOR_COOKIE_MAX_AGE,
    SEVERITIES,
    STATE_LABELS,
    action_from,
    chat_version,
    decode_author,
    encode_author,
    header_of,
    is_agent,
    is_waiting,
    parse_form,
    safe_redirect,
)

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
    EventType.VERIFICATION_REQUESTED: "Validação pedida",
    EventType.VERIFICATION_COMPLETED: "Validação concluída",
    EventType.VERIFICATION_PARTIAL: "Validação parcial",
    EventType.COMMANDER_ASSIGNED: "IC assumido",
    EventType.ACTION_RECORDED: "Ação registrada",
    EventType.HYPOTHESIS_DISCARDED: "Hipótese descartada",
    EventType.INCIDENT_MITIGATED: "Incidente mitigado",
    EventType.INCIDENT_REOPENED: "Incidente reaberto",
    EventType.INCIDENT_RESOLVED: "Incidente encerrado",
    EventType.INCIDENT_DISCARDED: "Incidente descartado",
    EventType.HUMAN_MESSAGE: "Mensagem no chat",
    EventType.AGENT_MESSAGE: "Resposta do agente",
}

ANALYSIS_STATUS_LABELS = {
    AnalysisStatus.PENDING: "análise em andamento",
    AnalysisStatus.RUNNING: "análise em andamento",
    AnalysisStatus.COMPLETED: "análise concluída",
    AnalysisStatus.PARTIAL: "análise parcial",
}

VERIFICATION_STATUS_LABELS = {
    AnalysisStatus.PENDING: "validando as hipóteses",
    AnalysisStatus.RUNNING: "validando as hipóteses",
    AnalysisStatus.COMPLETED: "validação concluída",
    AnalysisStatus.PARTIAL: "validação parcial",
}

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
TEMPLATE_GLOBALS: dict[str, Any] = {
    "state_labels": STATE_LABELS,
    "severities": SEVERITIES,
    "is_agent": is_agent,
    "is_waiting": is_waiting,
}
templates.env.globals.update(TEMPLATE_GLOBALS)

NO_AUTHOR = "informe seu nome"
WAR_ROOM_ERROR = "#war-room-error"
CHAT_ERROR = "#chat-error"


def error_fragment(message: str, status_code: int, target: str) -> HTMLResponse:
    """Erro mostrado na página: o HTMX troca só a área de erro do form (HX-Retarget)."""
    return HTMLResponse(
        f'<p class="error">{escape(message)}</p>',
        status_code=status_code,
        headers={"HX-Retarget": target, "HX-Reswap": "innerHTML"},
    )


@dataclass(frozen=True)
class Services:
    """O que as rotas usam; nos testes, montado com os fakes dos ports."""

    database_is_up: HealthCheck
    latest_heartbeat: LatestHeartbeat
    ingress: AlertIngress
    repository: IncidentRepository
    intake: IntakePolicy
    clock: Clock


def create_app(services: Services) -> FastAPI:  # noqa: PLR0915 — uma função curta por rota
    app = FastAPI(title="agent-grolla")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    repository = services.repository

    async def incident_or_404(incident_id: str) -> Incident:
        try:
            found = await repository.get_incident(UUID(incident_id))
        except ValueError:
            found = None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado")
        return found

    async def war_room_context(incident: Incident, author: str | None) -> dict[str, Any]:
        return {
            "incident": incident,
            "header": header_of(incident, services.clock.now(), author),
            "author": author,
            "analysis": await repository.analysis_of(incident.id),
            "verification": await repository.verification_of(incident.id),
        }

    async def chat_context(incident: Incident) -> dict[str, Any]:
        messages = await repository.chat_of(incident.id)
        return {"incident": incident, "messages": messages, "chat_version": chat_version(messages)}

    async def timeline_context(incident: Incident) -> dict[str, Any]:
        events = await repository.events_of(incident.id)
        return {"incident": incident, "events": events, "event_labels": EVENT_LABELS}

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
        await receive_alerts(alerts, repository, services.intake, services.clock)
        return {"status": "accepted"}

    @app.get("/incidents", response_class=HTMLResponse)
    async def incidents(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await repository.list_incidents(INCIDENT_LIST_LIMIT)
        return templates.TemplateResponse(request, "incidents.html", {"incidents": found})

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    async def incident(request: Request, incident_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await incident_or_404(incident_id)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        context = {
            **await war_room_context(found, author),
            **await chat_context(found),
            **await timeline_context(found),
            "alerts": await repository.alerts_of(found.id),
            "analysis_labels": ANALYSIS_STATUS_LABELS,
            "verification_labels": VERIFICATION_STATUS_LABELS,
            "partial_reasons": PARTIAL_REASON_TEXT,  # mesmo texto do card
        }
        return templates.TemplateResponse(request, "incident.html", context)

    @app.post("/incidents/{incident_id}/actions/{name}", response_class=HTMLResponse)
    async def incident_action(request: Request, incident_id: str, name: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, WAR_ROOM_ERROR)
        found = await incident_or_404(incident_id)
        try:
            action = action_from(name, parse_form(await request.body()))
            if action is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "ação desconhecida")
            updated = await act(
                repository=repository,
                incident_id=found.id,
                action=action,
                author=author,
                clock=services.clock,
                page_url=services.intake.page_url,
            )
        except IncidentNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado") from error
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, WAR_ROOM_ERROR)
        context = await war_room_context(updated, author)
        return templates.TemplateResponse(request, "_war_room.html", context)

    @app.get("/incidents/{incident_id}/chat", response_class=HTMLResponse)
    async def chat(request: Request, incident_id: str, visto: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        context = await chat_context(await incident_or_404(incident_id))
        if visto == context["chat_version"]:
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # nada novo: o HTMX não troca
        return templates.TemplateResponse(request, "_chat_messages.html", context)

    @app.post("/incidents/{incident_id}/chat", response_class=HTMLResponse)
    async def chat_message(request: Request, incident_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, CHAT_ERROR)
        found = await incident_or_404(incident_id)
        text = parse_form(await request.body()).get("texto", "")
        try:
            await ask(repository, found.id, author, text, services.clock)
        except IncidentNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado") from error
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, CHAT_ERROR)
        context = {**await chat_context(found), "author": author}
        return templates.TemplateResponse(request, "_chat.html", context)

    @app.get("/incidents/{incident_id}/timeline", response_class=HTMLResponse)
    async def timeline(request: Request, incident_id: str, visto: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        context = await timeline_context(await incident_or_404(incident_id))
        if visto == str(len(context["events"])):
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # nada novo: o HTMX não troca
        return templates.TemplateResponse(request, "_timeline.html", context)

    @app.post("/author")
    async def set_author(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        form = parse_form(await request.body())
        name = decode_author(encode_author(form.get("nome", "")))
        if name is None:
            return HTMLResponse(NO_AUTHOR, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)
        response = RedirectResponse(
            safe_redirect(form.get("voltar", "")), status_code=status.HTTP_303_SEE_OTHER
        )
        # Não é autenticação (fase 1, rede interna): só identifica o autor das ações e mensagens.
        response.set_cookie(
            AUTHOR_COOKIE,
            encode_author(name),
            max_age=AUTHOR_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    return app
