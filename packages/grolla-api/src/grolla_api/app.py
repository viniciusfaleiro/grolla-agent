import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from grolla.application import follow_up
from grolla.application.clock import Clock
from grolla.application.console import (
    ConsoleStore,
    ask_console,
    chart_series,
    open_incident_from_console,
)
from grolla.application.incidents import (
    AlertIngress,
    IncidentRepository,
    IntakePolicy,
    InvalidAlertPayloadError,
    receive_alerts,
)
from grolla.application.investigation import Telemetry, ToolUnavailableError
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
from grolla.application.metrics import (
    MetricsReader,
    agent_metrics,
    answer_seen_before,
    evaluate_agent,
)
from grolla.application.post_mortem import (
    DocumentRenderer,
    PostMortemFilters,
    PostMortemNotFoundError,
    PostMortemStore,
    RenderError,
    add_action,
    edit_section,
    move_status,
    overdue_items,
    render_pdf,
    request_regeneration,
    update_action,
)
from grolla.application.safety import MessageCounter
from grolla.application.transcript import (
    TranscriptPolicy,
    TranscriptStore,
    retry_transcript,
    upload_transcript,
)
from grolla.application.triage import merge_incidents
from grolla.application.war_room import IncidentNotFoundError, act, ask
from grolla.domain.analysis import PARTIAL_REASON_TEXT, AnalysisStatus
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.console import ConsoleMessage, ConsoleSession
from grolla.domain.incidents import (
    OPEN_STATES,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Severity,
)
from grolla.domain.memory import ENVIRONMENT, Memory, SubjectKind
from grolla.domain.post_mortem import (
    SECTION_TITLES,
    InvalidPostMortemError,
    PostMortem,
    SectionKey,
)
from grolla.domain.war_room import InvalidActionError
from starlette.datastructures import UploadFile

from grolla_api.console_pages import (
    CONSOLE_SUGGESTIONS,
    ChartView,
    chart_view,
    console_suggestions,
    console_version,
)
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
from grolla_api.metrics_pages import (
    GRANULARITY_LABELS,
    evaluation_from,
    granularity_of,
    hit_rate,
    is_empty,
    median_minutes,
    resolved_bars,
    yes_no_label,
)
from grolla_api.post_mortem_pages import (
    ACTION_KIND_LABELS,
    ACTION_STATUS_LABELS,
    PM_STATUS_LABELS,
    TRANSITIONS,
    ListExtras,
    can_edit,
    can_regenerate,
    changed_action,
    has_pdf,
    list_extras,
    list_filters,
    new_action,
    not_generated,
    outline_of,
    pm_row,
    section_key,
    target_status,
    totals_of,
)
from grolla_api.war_room import (
    AUTHOR_COOKIE,
    AUTHOR_COOKIE_MAX_AGE,
    CLOSED_SHOWN,
    SEVERITIES,
    STATE_LABELS,
    SUGGESTIONS,
    SYSTEM_AUTHORS,
    TOOL_LABELS,
    UTF8_MAX_BYTES,
    ClosedRow,
    IncidentFilter,
    action_from,
    ago,
    alarm_normalized,
    alarm_of,
    chat_version,
    closed_row,
    current_proposal,
    decode_author,
    discarded_hypotheses,
    display_name,
    encode_author,
    event_detail,
    event_kind,
    event_label,
    failed_transcript,
    firing_count,
    from_transcript,
    header_of,
    hypothesis_rows,
    initials,
    is_agent,
    is_failure,
    is_quiet,
    is_waiting,
    linked_ids,
    linked_incident,
    merge_candidates,
    merge_suggestion,
    merge_target,
    merged_into,
    open_card,
    parse_form,
    partial_text,
    percent,
    reading_transcript,
    recent_cycles,
    rich_text,
    safe_redirect,
    same_problem,
    seen_answer_text,
    seen_before_answer,
    situation_of,
    system_count,
    transcript_file,
    trend_label,
    visible_events,
)

HealthCheck = Callable[[], Awaitable[bool]]
LatestHeartbeat = Callable[[], Awaitable[datetime | None]]

WORKER_HEARTBEAT_LIMIT = timedelta(minutes=2)
INCIDENT_LIST_LIMIT = 200
CONSOLE_SESSIONS_LIMIT = 50

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
    EventType.CYCLE_COMPLETED: "Acompanhamento",
    EventType.CYCLE_PARTIAL: "Acompanhamento parcial",
    EventType.FOLLOW_UP_STOPPED: "Acompanhamento parado",
    EventType.AGENT_PROPOSAL: "Proposta do Grolla",
    EventType.SEEN_BEFORE_ANSWERED: "Resposta ao já vi isso",
    EventType.TRIAGE_COMPLETED: "Triagem do Grolla",
    EventType.MERGE_SUGGESTED: "Grolla sugere juntar com",  # + o link do outro incidente
    EventType.MERGED_INTO: "Juntado em",
    EventType.INCIDENT_MERGED: "Recebeu o incidente",
    EventType.INCIDENT_OPENED_MANUALLY: "Aberto manualmente",  # pelo console: "Aberto pelo console"
    # O envio, os itens, as hipóteses e a falha montam o rótulo com o conteúdo (event_label).
    EventType.TRANSCRIPT_PARTICIPANTS: "Participantes da call",
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
    "is_empty": is_empty,
    "alarm_normalized": alarm_normalized,
    "display_name": display_name,
    "event_detail": event_detail,
    "event_kind": event_kind,
    "system_count": system_count,
    "event_label": event_label,
    "initials": initials,
    "is_agent": is_agent,
    "is_failure": is_failure,
    "is_quiet": is_quiet,
    "is_waiting": is_waiting,
    "linked_incident": linked_incident,
    "partial_text": partial_text,
    "percent": percent,
    "trend_label": trend_label,
    "rich_text": rich_text,
    "visible_events": visible_events,
    "from_transcript": from_transcript,
    "failed_transcript": failed_transcript,
    "reading_transcript": reading_transcript,
    "source_of": source_of,
    "study_modes": STUDY_MODE_LABELS,
    "study_statuses": STUDY_STATUS_LABELS,
    "new_subject_kinds": NEW_SUBJECT_KINDS,
    "subject_kind_names": SUBJECT_KIND_NAMES,
    "subject_url": subject_url,
    "pm_status_labels": PM_STATUS_LABELS,
    "action_kind_labels": ACTION_KIND_LABELS,
    "action_status_labels": ACTION_STATUS_LABELS,
    "section_titles": SECTION_TITLES,
    "transitions": TRANSITIONS,
    "can_edit": can_edit,
    "can_regenerate": can_regenerate,
    "has_pdf": has_pdf,
    "not_generated": not_generated,
    "seen_answer_text": seen_answer_text,
    "yes_no_label": yes_no_label,
    "granularity_labels": GRANULARITY_LABELS,
    "hit_rate": hit_rate,
    "median_minutes": median_minutes,
    "console_suggestions": CONSOLE_SUGGESTIONS,
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
STATUS_ERROR = "#erro-status"
PLAN_ERROR = "#erro-plano"
EVALUATION_ERROR = "#erro-avaliacao"
SEEN_BEFORE_ERROR = "#erro-ja-vi-isso"
CONSOLE_ERROR = "#console-error"
CONSOLE_INCIDENT_ERROR = "#erro-incidente"
CONSOLE_OFF = "o console está desligado nesta instância"
INVALID_SEVERITY = "escolha a severidade: P0, P1, P2 ou P3"
REGENERATION_ASKED = (
    "Pedido enviado: o Grolla está regenerando esta seção. Recarregue a página em instantes "
    "para ver o texto novo; o atual fica no histórico."
)
PDF_FAILED = "não consegui gerar o PDF; tente de novo em instantes"
TRANSCRIPT_ERROR = "#erro-transcricao"  # dentro do popover de envio
TRANSCRIPT_OFF = "a transcrição está desligada nesta instância"
TRANSCRIPT_NOT_FOUND = "transcrição não encontrada"


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
    post_mortems: PostMortemStore
    renderer: DocumentRenderer
    metrics: MetricsReader
    messages: MessageCounter  # limite de mensagens por pessoa no chat e no console
    impact: follow_up.ImpactPolicy = field(default_factory=follow_up.ImpactPolicy)
    console: ConsoleStore | None = None  # None: console desligado (sem a seção `console`)
    telemetry: Telemetry | None = None  # gráficos do console; sem ela, "gráfico indisponível"
    transcripts: TranscriptStore | None = None  # None: envio de transcrição dá 404
    transcript_policy: TranscriptPolicy = field(default_factory=TranscriptPolicy)
    chat_messages_per_hour: int = 30  # por autor, chat e console juntos


# Mudança num registro: (registro, autor, campos do form) → registro gravado.
RecordChange = Callable[[Memory, str, dict[str, str]], Awaitable[Memory]]
# Mudança num post mortem: (documento, autor, campos do form) → o que o caso de uso devolve.
PostMortemChange = Callable[[PostMortem, str, dict[str, str]], Awaitable[object]]


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

    async def follow_up_context(incident: Incident, author: str | None) -> dict[str, Any]:
        """O que muda sozinho: estado, alarme, alertas, acompanhamento, impacto e proposta; no
        war room e no polling da timeline.
        """
        events = await repository.events_of(incident.id)
        recent = await repository.list_incidents(INCIDENT_LIST_LIMIT)
        return {
            "incident": incident,
            "header": header_of(incident, services.clock.now(), author),
            "author": author,
            "now": services.clock.now(),
            "alarm": alarm_of(incident, events),
            "alerts": await repository.alerts_of(incident.id),
            "cycles": recent_cycles(await repository.cycles_of(incident.id)),
            "impact": await follow_up.impact_of(repository, incident, services.impact),
            "proposal": current_proposal(incident, events),
            "merge_candidates": merge_candidates(incident, recent),
            "firing_count": firing_count(recent),
        }

    async def linked_of(events: list[IncidentEvent]) -> dict[UUID, Incident]:
        """Os incidentes citados pelos eventos da junção, para o título e o link."""
        found = [await repository.get_incident(ref) for ref in linked_ids(events)]
        return {incident.id: incident for incident in found if incident is not None}

    async def war_room_context(incident: Incident, author: str | None) -> dict[str, Any]:
        analysis = await repository.analysis_of(incident.id)
        events = await repository.events_of(incident.id)
        linked = await linked_of(events)
        seen = analysis.seen_before if analysis else None
        verification = await repository.verification_of(incident.id)
        return {
            **await follow_up_context(incident, author),
            "analysis": analysis,
            "verification": verification,
            "situation": situation_of(analysis, verification),
            "hypotheses": hypothesis_rows(analysis, verification),
            "discarded": discarded_hypotheses(events),
            "seen_answer": seen_before_answer(events, seen.incident_id) if seen else None,
            "merge": merge_suggestion(incident, events, linked),
            "merged_into": merged_into(events, linked),
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
        return {
            "incident": incident,
            "events": events,
            "event_labels": EVENT_LABELS,
            "linked": await linked_of(events),
        }

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
    async def incidents(request: Request, mais: str = "") -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await repository.list_incidents(INCIDENT_LIST_LIMIT)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        now = services.clock.now()
        params: dict[str, list[str]] = {}
        for key, value in request.query_params.multi_items():
            params.setdefault(key, []).append(value)
        chosen = IncidentFilter.from_query(params)
        shown = [i for i in found if chosen.matches(i, author, now)]
        opened = [i for i in shown if i.state in OPEN_STATES and chosen.scope != "encerrados"]
        closed = [i for i in shown if i.state not in OPEN_STATES and chosen.scope != "abertos"]
        visible_closed = closed if mais else closed[:CLOSED_SHOWN]
        cards = [
            open_card(
                i,
                await repository.analysis_of(i.id),
                await repository.verification_of(i.id),
                now,
            )
            for i in opened
        ]
        rows: list[ClosedRow] = []
        for i in visible_closed:
            pm = await services.post_mortems.get(i.id)
            rows.append(
                closed_row(
                    i,
                    await repository.verification_of(i.id),
                    PM_STATUS_LABELS[pm.status] if pm else "",
                )
            )
        context = {
            "incidents": found,
            "cards": cards,
            "rows": rows,
            "hidden_closed": len(closed) - len(visible_closed),
            "filters": chosen,
            "query": params,
            "services_seen": sorted({s for i in found for s in i.services}),
            "shown_count": len(opened) + len(closed),
            "firing_count": firing_count(found),
            "author": author,
            "now": now,
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

    @app.post("/incidents/{incident_id}/ja-vi-isso")
    async def seen_before(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, SEEN_BEFORE_ERROR)
        found = await incident_or_404(incident_id)
        try:
            await answer_seen_before(
                repository=repository,
                incident_id=found.id,
                same=same_problem(parse_form(await request.body())),
                author=author,
                clock=services.clock,
            )
        except IncidentNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado") from error
        except InvalidActionError as error:
            return error_fragment(
                str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, SEEN_BEFORE_ERROR
            )
        return reload_page(request, f"/incidents/{found.id}")

    @app.post("/incidents/{incident_id}/juntar")
    async def merge(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        """Junta este incidente no `destino` e leva ao war room do principal."""
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, WAR_ROOM_ERROR)
        found = await incident_or_404(incident_id)
        try:
            target = await merge_incidents(
                repository=repository,
                source_id=found.id,
                target_id=merge_target(parse_form(await request.body())),
                author=author,
                clock=services.clock,
                page_url=services.intake.page_url,
            )
        except IncidentNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado") from error
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, WAR_ROOM_ERROR)
        return reload_page(request, f"/incidents/{target.id}")

    def transcripts_or_404() -> TranscriptStore:
        if services.transcripts is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, TRANSCRIPT_OFF)
        return services.transcripts

    async def transcript_sent(request: Request) -> tuple[str, str | None]:
        """O texto e o nome do arquivo: o arquivo, se veio, vale mais que o texto colado."""
        limit = services.transcript_policy.max_chars * UTF8_MAX_BYTES
        async with request.form(max_files=1, max_part_size=limit) as form:  # fecha o arquivo
            upload = form.get("arquivo")
            if isinstance(upload, UploadFile) and upload.filename:
                content = await upload.read(limit + 1)
                max_chars = services.transcript_policy.max_chars
                return transcript_file(upload.filename, content, max_chars), upload.filename
            text = form.get("texto")
            return (text if isinstance(text, str) else ""), None

    @app.post("/incidents/{incident_id}/transcricao")
    async def send_transcript(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        """Guarda a transcrição da call e pede a leitura ao worker."""
        store = transcripts_or_404()
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, TRANSCRIPT_ERROR)
        found = await incident_or_404(incident_id)
        try:
            raw, filename = await transcript_sent(request)
            await upload_transcript(
                repository=repository,
                store=store,
                incident_id=found.id,
                author=author,
                raw=raw,
                filename=filename,
                clock=services.clock,
                policy=services.transcript_policy,
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente não encontrado") from error
        except InvalidActionError as error:
            return error_fragment(
                str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, TRANSCRIPT_ERROR
            )
        return reload_page(request, f"/incidents/{found.id}")

    @app.post("/incidents/{incident_id}/transcricao/{transcript_id}/reprocessar")
    async def retry_failed_transcript(  # pyright: ignore[reportUnusedFunction]
        request: Request, incident_id: str, transcript_id: str
    ) -> Response:
        store = transcripts_or_404()
        target = f"#erro-transcricao-{escape(transcript_id)}"
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, target)
        found = await incident_or_404(incident_id)
        transcript = await store.get(int(transcript_id)) if transcript_id.isdigit() else None
        if transcript is None or transcript.incident_id != found.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, TRANSCRIPT_NOT_FOUND)
        try:
            await retry_transcript(
                store=store, transcript_id=transcript.id, author=author, clock=services.clock
            )
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, target)
        return reload_page(request, f"/incidents/{found.id}")

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
        found = await incident_or_404(incident_id)
        context = await timeline_context(found)
        if visto == str(len(context["events"])):
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # nada novo: o HTMX não troca
        # Ciclo e proposta chegam como evento: o acompanhamento vem junto, fora da timeline.
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        context |= {**await follow_up_context(found, author), "oob": True}
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
            "groups": subject_groups(await memory.subjects()),
            "now": clock.now(),
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

    post_mortems = services.post_mortems

    def pm_url(incident_id: UUID) -> str:
        return f"/postmortems/{incident_id}"

    def key_or_404(key: str) -> SectionKey:
        section = section_key(key)
        if section is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "seção não encontrada")
        return section

    async def post_mortem_or_404(incident_id: str) -> PostMortem:
        found = await post_mortems.get((await incident_or_404(incident_id)).id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "post mortem não encontrado")
        return found

    async def change_post_mortem(
        request: Request,
        incident_id: str,
        target: str,
        change: PostMortemChange,
        confirmation: str | None = None,
    ) -> Response:
        """Escrita no post mortem: exige autor; erro de domínio vira mensagem junto do form.

        Sem `confirmation`, recarrega a página; com ela, o HTMX recebe só o aviso (`202`).
        """
        pm = await post_mortem_or_404(incident_id)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, target)
        try:
            await change(pm, author, parse_form(await request.body()))
        except PostMortemNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "post mortem não encontrado") from error
        except InvalidPostMortemError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, target)
        if confirmation and request.headers.get("HX-Request") == "true":
            return HTMLResponse(
                f'<p class="notice">{escape(confirmation)}</p>',
                status_code=status.HTTP_202_ACCEPTED,
            )
        return reload_page(request, pm_url(pm.incident_id))

    @app.get("/postmortems", response_class=HTMLResponse)
    async def post_mortem_list(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        filters = list_filters(request.query_params)
        extras = list_extras(request.query_params)
        today = clock.now().date()
        everything = await post_mortems.summaries(PostMortemFilters(), today)
        found = await post_mortems.summaries(filters, today)
        rows = [pm_row(s, await post_mortems.get(s.incident.id)) for s in found]
        rows = [row for row in rows if extras.matches(row)]
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "postmortems",
            "back_to": "/postmortems",
            "filters": filters,
            "extras": extras,
            "filtered": filters != PostMortemFilters() or extras != ListExtras(),
            "summaries": [row.summary for row in rows],
            "rows": rows,
            "total": len(everything),
            "totals": totals_of(everything, today),
            "services_seen": sorted({x for s in everything for x in s.incident.services}),
        }
        return templates.TemplateResponse(request, "postmortems.html", context)

    # Antes da página: `/postmortems/{id}` também casaria com "<id>.pdf".
    @app.get("/postmortems/{incident_id}.pdf")
    async def post_mortem_pdf(incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        pm = await post_mortem_or_404(incident_id)
        try:
            pdf = await render_pdf(
                store=post_mortems,
                repository=repository,
                renderer=services.renderer,
                incident_id=pm.incident_id,
            )
        except PostMortemNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "post mortem não encontrado") from error
        except InvalidPostMortemError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except RenderError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, PDF_FAILED) from error
        filename = f"post-mortem-{pm.incident_id}.pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/postmortems/{incident_id}", response_class=HTMLResponse)
    async def post_mortem_page(request: Request, incident_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        found = await incident_or_404(incident_id)
        pm = await post_mortems.get(found.id)
        overdue = overdue_items(pm, clock.now().date()) if pm else ()
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "postmortems",
            "back_to": pm_url(found.id),
            "incident": found,
            "resolved": found.state is IncidentState.RESOLVED,
            "pm": pm,
            "overdue": {item.id for item in overdue},
            "outline": outline_of(pm, len(overdue)) if pm else (),
            "header": header_of(found, clock.now()),
        }
        return templates.TemplateResponse(request, "postmortem.html", context)

    @app.get("/postmortems/{incident_id}/secoes/{key}/versoes", response_class=HTMLResponse)
    async def post_mortem_versions(request: Request, incident_id: str, key: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        section = key_or_404(key)
        pm = await post_mortem_or_404(incident_id)
        context = {"versions": await post_mortems.versions(pm.incident_id, section)}
        return templates.TemplateResponse(request, "_post_mortem_versions.html", context)

    @app.post("/postmortems/{incident_id}/secoes/{key}")
    async def post_mortem_edit(request: Request, incident_id: str, key: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        section = key_or_404(key)

        async def edited(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            return await edit_section(
                store=post_mortems,
                incident_id=pm.incident_id,
                key=section,
                body=form.get("texto", ""),
                author=author,
                clock=clock,
            )

        return await change_post_mortem(request, incident_id, f"#erro-{section}", edited)

    @app.post("/postmortems/{incident_id}/secoes/{key}/regenerar")
    async def post_mortem_regenerate(request: Request, incident_id: str, key: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        section = key_or_404(key)

        async def asked(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            return await request_regeneration(
                store=post_mortems,
                incident_id=pm.incident_id,
                key=section,
                instruction=form.get("instrucao") or None,
                author=author,
                clock=clock,
            )

        target = f"#erro-{section}"
        return await change_post_mortem(request, incident_id, target, asked, REGENERATION_ASKED)

    @app.post("/postmortems/{incident_id}/status")
    async def post_mortem_status(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def moved(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            return await move_status(
                store=post_mortems,
                incident_id=pm.incident_id,
                to=target_status(form),
                author=author,
                clock=clock,
                repository=repository,  # aprovar pede o aprendizado com o post mortem
            )

        return await change_post_mortem(request, incident_id, STATUS_ERROR, moved)

    @app.post("/postmortems/{incident_id}/avaliacao")
    async def post_mortem_evaluation(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def evaluated(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            return await evaluate_agent(
                store=post_mortems,
                incident_id=pm.incident_id,
                evaluation=evaluation_from(form),
                author=author,
                clock=clock,
            )

        return await change_post_mortem(request, incident_id, EVALUATION_ERROR, evaluated)

    @app.post("/postmortems/{incident_id}/plano")
    async def post_mortem_add_action(request: Request, incident_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        async def added(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            fields = new_action(form)
            return await add_action(
                store=post_mortems,
                incident_id=pm.incident_id,
                description=fields.description,
                kind=fields.kind,
                owner=fields.owner,
                due=fields.due,
                author=author,
                clock=clock,
            )

        return await change_post_mortem(request, incident_id, PLAN_ERROR, added)

    @app.post("/postmortems/{incident_id}/plano/{action_id}")
    async def post_mortem_update_action(  # pyright: ignore[reportUnusedFunction]
        request: Request, incident_id: str, action_id: str
    ) -> Response:
        async def updated(pm: PostMortem, author: str, form: dict[str, str]) -> object:
            item = next((a for a in pm.actions if str(a.id) == action_id), None)
            if item is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "item do plano não encontrado")
            fields = changed_action(item, form)
            return await update_action(
                store=post_mortems,
                incident_id=pm.incident_id,
                action_id=item.id,
                description=fields.description,
                kind=fields.kind,
                owner=fields.owner,
                due=fields.due,
                status=fields.status,
                author=author,
                clock=clock,
            )

        return await change_post_mortem(request, incident_id, PLAN_ERROR, updated)

    @app.get("/metricas", response_class=HTMLResponse)
    async def metrics(request: Request, por: str = "") -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        granularity = granularity_of(por)
        rows = await agent_metrics(services.metrics, clock, granularity)
        summaries = await post_mortems.summaries(PostMortemFilters(), clock.now().date())
        docs = [await post_mortems.get(s.incident.id) for s in summaries]
        unevaluated = sum(1 for pm in docs if pm is not None and pm.evaluation.author is None)
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "metrics",
            "back_to": f"/metricas?por={granularity}",
            "granularity": granularity,
            "rows": rows,
            "bars": resolved_bars(rows),
            "unevaluated": unevaluated,
        }
        return templates.TemplateResponse(request, "metrics.html", context)

    def console_or_404() -> ConsoleStore:
        if services.console is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, CONSOLE_OFF)
        return services.console

    async def console_session_or_404(session_id: str) -> ConsoleSession:
        store = console_or_404()
        try:
            found = await store.session(UUID(session_id))
        except ValueError:
            found = None
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversa não encontrada")
        return found

    async def charts_of(messages: list[ConsoleMessage]) -> dict[int, list[ChartView]]:
        """Cada gráfico pedido, com a série lida agora; falha vira "gráfico indisponível"."""
        views: dict[int, list[ChartView]] = {}
        for message in messages:
            for chart in message.charts:
                points = None
                if services.telemetry is not None:
                    try:
                        points = await chart_series(services.telemetry, chart, clock)
                    except ToolUnavailableError:
                        points = None
                views.setdefault(message.id, []).append(chart_view(chart, points))
        return views

    async def console_context(session: ConsoleSession, author: str | None) -> dict[str, Any]:
        store = console_or_404()
        messages = await store.messages(session.id)
        return {
            "session": session,
            "author": author,
            "messages": messages,
            "charts": await charts_of(messages),
            "console_version": console_version(messages),
        }

    async def known_services() -> list[str]:
        """Os serviços que a memória conhece, os atualizados mais recentemente primeiro."""
        subjects = await memory.subjects()
        found = [s for s in subjects if s.kind is SubjectKind.SERVICE]
        return [s.subject for s in sorted(found, key=lambda s: s.updated_at, reverse=True)]

    @app.get("/console", response_class=HTMLResponse)
    async def console_home(request: Request) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        store = services.console
        context = {
            "author": decode_author(request.cookies.get(AUTHOR_COOKIE)),
            "nav": "console",
            "back_to": "/console",
            "now": clock.now(),
            "enabled": store is not None,
            "sessions": await store.sessions(CONSOLE_SESSIONS_LIMIT) if store else [],
            "console_suggestions": console_suggestions(await known_services()),
        }
        return templates.TemplateResponse(request, "console.html", context)

    @app.post("/console")
    async def console_new(request: Request) -> Response:  # pyright: ignore[reportUnusedFunction]
        store = console_or_404()
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, CONSOLE_ERROR)
        text = parse_form(await request.body()).get("texto", "")
        try:
            session = await ask_console(
                store=store, session_id=None, author=author, text=text, clock=clock
            )
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, CONSOLE_ERROR)
        return reload_page(request, f"/console/{session.id}")

    @app.get("/console/{session_id}", response_class=HTMLResponse)
    async def console_page(request: Request, session_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        session = await console_session_or_404(session_id)
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        incident = (
            await repository.get_incident(session.incident_id) if session.incident_id else None
        )
        context = {
            **await console_context(session, author),
            "nav": "console",
            "back_to": f"/console/{session.id}",
            "incident": incident,
            "sessions": await console_or_404().sessions(CONSOLE_SESSIONS_LIMIT),
            "now": clock.now(),
        }
        return templates.TemplateResponse(request, "console_session.html", context)

    @app.get("/console/{session_id}/mensagens", response_class=HTMLResponse)
    async def console_messages(request: Request, session_id: str, visto: str = "") -> Response:  # pyright: ignore[reportUnusedFunction]
        session = await console_session_or_404(session_id)
        store = console_or_404()
        if visto == console_version(await store.messages(session.id)):
            return Response(status_code=status.HTTP_204_NO_CONTENT)  # nada novo: o HTMX não troca
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        context = await console_context(session, author)
        return templates.TemplateResponse(request, "_console_messages.html", context)

    @app.post("/console/{session_id}", response_class=HTMLResponse)
    async def console_ask(request: Request, session_id: str) -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
        store = console_or_404()
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, CONSOLE_ERROR)
        session = await console_session_or_404(session_id)
        text = parse_form(await request.body()).get("texto", "")
        try:
            await ask_console(
                store=store, session_id=session.id, author=author, text=text, clock=clock
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversa não encontrada") from error
        except InvalidActionError as error:
            return error_fragment(str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, CONSOLE_ERROR)
        context = await console_context(session, author)
        return templates.TemplateResponse(request, "_console_messages.html", context)

    @app.post("/console/{session_id}/incidente")
    async def console_incident(request: Request, session_id: str) -> Response:  # pyright: ignore[reportUnusedFunction]
        store = console_or_404()
        author = decode_author(request.cookies.get(AUTHOR_COOKIE))
        if author is None:
            return error_fragment(NO_AUTHOR, status.HTTP_401_UNAUTHORIZED, CONSOLE_INCIDENT_ERROR)
        session = await console_session_or_404(session_id)
        form = parse_form(await request.body())
        try:
            severity = Severity(form.get("severidade", ""))
        except ValueError:
            return error_fragment(
                INVALID_SEVERITY, status.HTTP_422_UNPROCESSABLE_CONTENT, CONSOLE_INCIDENT_ERROR
            )
        try:
            opened = await open_incident_from_console(
                store=store,
                repository=repository,
                session_id=session.id,
                title=form.get("titulo", ""),
                service=form.get("servico", ""),
                severity=severity,
                author=author,
                clock=clock,
                page_url=services.intake.page_url,
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "conversa não encontrada") from error
        except (InvalidActionError, ValueError) as error:
            return error_fragment(
                str(error), status.HTTP_422_UNPROCESSABLE_CONTENT, CONSOLE_INCIDENT_ERROR
            )
        return reload_page(request, f"/incidents/{opened.id}")

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
