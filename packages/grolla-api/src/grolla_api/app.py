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
from grolla.application.memory import (
    InvalidMemoryError,
    LearningQueue,
    MemoryNotFoundError,
    StudyMode,
    StudyQueue,
    add_note,
    confirm_memory,
    edit_memory,
    forget_memory,
    request_study,
    restore_memory,
)
from grolla.application.memory_store import MemoryStore
from grolla.application.war_room import IncidentNotFoundError, act, ask
from grolla.domain.analysis import PARTIAL_REASON_TEXT, AnalysisStatus
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.incidents import OPEN_STATES, EventType, Incident
from grolla.domain.memory import ENVIRONMENT, Memory, SubjectKind
from grolla.domain.war_room import InvalidActionError

from grolla_api.memory_pages import (
    NEW_SUBJECT_KINDS,
    STUDY_MODE_LABELS,
    STUDY_STATUS_LABELS,
    SUBJECT_KIND_NAMES,
    SUBJECT_MAX_LENGTH,
    kind_of,
    parse_version_at,
    source_of,
    split_records,
    study_progress,
    subject_groups,
    subject_url,
)
from grolla_api.war_room import (
    AUTHOR_COOKIE,
    AUTHOR_COOKIE_MAX_AGE,
    SEVERITIES,
    STATE_LABELS,
    SUGGESTIONS,
    SYSTEM_AUTHORS,
    TOOL_LABELS,
    action_from,
    ago,
    chat_version,
    decode_author,
    discarded_hypotheses,
    display_name,
    encode_author,
    event_detail,
    header_of,
    initials,
    is_agent,
    is_failure,
    is_quiet,
    is_waiting,
    parse_form,
    rich_text,
    safe_redirect,
    visible_events,
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
    EventType.NOTIFICATION_DELIVERED: "Card enviado ao canal",
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
    "open_states": OPEN_STATES,
    "agent_author": AGENT_AUTHOR,
    "system_authors": SYSTEM_AUTHORS,
    "tool_labels": TOOL_LABELS,
    "suggestions": SUGGESTIONS,
    "analysis_labels": ANALYSIS_STATUS_LABELS,
    "verification_labels": VERIFICATION_STATUS_LABELS,
    "partial_reasons": PARTIAL_REASON_TEXT,  # mesmo texto do card
    "ago": ago,
    "display_name": display_name,
    "event_detail": event_detail,
    "initials": initials,
    "is_agent": is_agent,
    "is_failure": is_failure,
    "is_quiet": is_quiet,
    "is_waiting": is_waiting,
    "rich_text": rich_text,
    "visible_events": visible_events,
    "source_of": source_of,
    "study_modes": STUDY_MODE_LABELS,
    "study_statuses": STUDY_STATUS_LABELS,
    "new_subject_kinds": NEW_SUBJECT_KINDS,
    "subject_kind_names": SUBJECT_KIND_NAMES,
    "subject_url": subject_url,
}
templates.env.globals.update(TEMPLATE_GLOBALS)

NO_AUTHOR = "informe seu nome"
WAR_ROOM_ERROR = "#war-room-error"
CHAT_ERROR = "#chat-error"
STUDY_ERROR = "#erro-estudo"
RELEARN_ERROR = "#erro-reaprender"  # dentro do popover de confirmação
NOTE_ERROR = "#erro-nota"
RELEARN_UNCONFIRMED = "confirme que quer reaprender o ambiente do zero"
STUDY_QUEUED = "já tem um estudo na fila ou em andamento; espere ele terminar"


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
    memory: MemoryStore
    studies: StudyQueue
    learning_queue: LearningQueue
    study_enabled: bool  # sem `memory.study` no YAML nenhum estudo roda


# Mudança num registro: (registro, autor, campos do form) → registro gravado.
RecordChange = Callable[[Memory, str, dict[str, str]], Awaitable[Memory]]


def reload_page(request: Request, url: str) -> Response:
    """A ação da memória deu certo: volta à página com o estado novo.

    O HTMX navega pelo `HX-Redirect` (um 303 seria seguido pelo fetch e trocado dentro do form);
    um form comum recebe o 303.
    """
    if request.headers.get("HX-Request") == "true":
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def subject_or_404(subject: str) -> str:
    subject = subject.strip()
    if not subject or len(subject) > SUBJECT_MAX_LENGTH:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "assunto não encontrado")
    return subject


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
            "discarded": discarded_hypotheses(await repository.events_of(incident.id)),
        }

    async def chat_context(incident: Incident, author: str | None) -> dict[str, Any]:
        messages = await repository.chat_of(incident.id)
        return {
            "incident": incident,
            "author": author,
            "messages": messages,
            "chat_version": chat_version(messages),
        }

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
        context = {
            "incidents": found,
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "now": services.clock.now(),
            "nav": "incidents",
        }
        return templates.TemplateResponse(request, "incidents.html", context)

    @app.get("/incidents/{incident_id}", response_class=HTMLResponse)
    async def incident(request: Request, incident_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await incident_or_404(incident_id)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        context = {
            **await chat_context(found, author),
            **await timeline_context(found),
            **await war_room_context(found, author),
            "alerts": await repository.alerts_of(found.id),
            "back_to": f"/incidents/{found.id}",
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
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        context = await chat_context(await incident_or_404(incident_id), author)
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
        context = await chat_context(found, author)
        return templates.TemplateResponse(request, "_chat_messages.html", context)

    @app.get("/incidents/{incident_id}/timeline", response_class=HTMLResponse)
    async def timeline(request: Request, incident_id: str, visto: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        context = await timeline_context(await incident_or_404(incident_id))
        if visto == str(len(context["events"])):
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # nada novo: o HTMX não troca
        return templates.TemplateResponse(request, "_timeline.html", context)

    memory = services.memory
    clock = services.clock

    async def record_or_404(memory_id: str) -> Memory:
        try:
            found = await memory.get(UUID(memory_id))
        except ValueError:
            found = None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "registro não encontrado")
        return found

    @app.get("/memoria", response_class=HTMLResponse)
    async def memory_home(request: Request, assunto: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        if assunto.strip():  # "abrir ou criar um assunto" leva à página dele
            url = subject_url(subject_or_404(assunto))
            return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "memory",
            "back_to": "/memoria",
            "now": clock.now(),
            "environment": await memory.dossier(ENVIRONMENT),
            "groups": subject_groups(await memory.subjects()),
            "study": await services.studies.last(),
            "progress": study_progress(await services.studies.services()),
            "learning_failures": await services.learning_queue.failed(),
            "study_enabled": services.study_enabled,
        }
        return templates.TemplateResponse(request, "memory.html", context)

    async def study(request: Request, mode: StudyMode, target: str) -> Response:
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, target)
        try:
            asked = await request_study(
                studies=services.studies, memory=memory, mode=mode, author=author, clock=clock
            )
        except InvalidMemoryError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, target)
        if not asked:
            return error_fragment(STUDY_QUEUED, status.HTTP_409_CONFLICT, target)
        return reload_page(request, "/memoria")

    @app.post("/memoria/estudar")
    async def study_again(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await study(request, StudyMode.FULL, STUDY_ERROR)

    @app.post("/memoria/reaprender")
    async def relearn(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        if parse_form(await request.body()).get("confirmar") != "sim":
            return error_fragment(
                RELEARN_UNCONFIRMED, status.HTTP_422_UNPROCESSABLE_CONTENT, RELEARN_ERROR
            )
        return await study(request, StudyMode.RELEARN, RELEARN_ERROR)

    @app.get("/memoria/registros/{memory_id}/versoes", response_class=HTMLResponse)
    async def memory_versions(request: Request, memory_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        record = await record_or_404(memory_id)
        context = {
            "record": record,
            "versions": await memory.versions(record.id),
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
        }
        return templates.TemplateResponse(request, "_memory_versions.html", context)

    async def change_record(request: Request, memory_id: str, change: RecordChange) -> Response:
        """Escrita num registro: exige autor; erro de domínio vira mensagem junto do registro."""
        record = await record_or_404(memory_id)
        target = f"#erro-{record.id}"
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, target)
        try:
            await change(record, author, parse_form(await request.body()))
        except MemoryNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "registro não encontrado") from error
        except InvalidMemoryError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, target)
        return reload_page(request, subject_url(record.subject))

    async def edited(record: Memory, author: str, form: dict[str, str]) -> Memory:
        body = form.get("texto", "")
        return await edit_memory(
            memory=memory, memory_id=record.id, body=body, author=author, clock=clock
        )

    async def confirmed(record: Memory, author: str, _: dict[str, str]) -> Memory:
        return await confirm_memory(memory=memory, memory_id=record.id, author=author, clock=clock)

    async def forgotten(record: Memory, author: str, _: dict[str, str]) -> Memory:
        return await forget_memory(memory=memory, memory_id=record.id, author=author, clock=clock)

    async def restored(record: Memory, author: str, form: dict[str, str]) -> Memory:
        version_at = parse_version_at(form.get("versao", ""))
        if version_at is None:
            raise InvalidMemoryError("versão inválida")
        return await restore_memory(
            memory=memory, memory_id=record.id, version_at=version_at, author=author, clock=clock
        )

    @app.post("/memoria/registros/{memory_id}")
    async def edit_record(request: Request, memory_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await change_record(request, memory_id, edited)

    @app.post("/memoria/registros/{memory_id}/confirmar")
    async def confirm_record(request: Request, memory_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await change_record(request, memory_id, confirmed)

    @app.post("/memoria/registros/{memory_id}/esquecer")
    async def forget_record(request: Request, memory_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await change_record(request, memory_id, forgotten)

    @app.post("/memoria/registros/{memory_id}/restaurar")
    async def restore_record(request: Request, memory_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        return await change_record(request, memory_id, restored)

    @app.get("/memoria/{subject}", response_class=HTMLResponse)
    async def memory_subject(request: Request, subject: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        subject = subject_or_404(subject)
        records = await memory.of_subject(subject)
        url = subject_url(subject)
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "memory",
            "back_to": url,
            "subject": subject,
            "page_url": url,
            "kind": kind_of(subject, records),
            "records": split_records(records),
        }
        return templates.TemplateResponse(request, "memory_subject.html", context)

    @app.post("/memoria/{subject}/notas")
    async def new_note(request: Request, subject: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        subject = subject_or_404(subject)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, NOTE_ERROR)
        form = parse_form(await request.body())
        kind = kind_of(subject, await memory.of_subject(subject))
        if kind is None:  # assunto novo: a nota diz se é serviço ou integração
            kind = (
                SubjectKind.INTEGRATION if form.get("tipo") == "integracao" else SubjectKind.SERVICE
            )
        try:
            await add_note(
                memory=memory,
                subject=subject,
                subject_kind=kind,
                text=form.get("texto", ""),
                author=author,
                clock=clock,
            )
        except InvalidMemoryError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, NOTE_ERROR)
        return reload_page(request, subject_url(subject))

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
