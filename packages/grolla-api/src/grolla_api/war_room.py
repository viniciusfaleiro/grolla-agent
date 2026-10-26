"""Apresentação do war room: rótulos, durações, ações por estado e leitura dos forms."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, unquote

from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.war_room import (
    AssumeCommander,
    ChangeSeverity,
    DiscardHypothesis,
    DiscardIncident,
    HumanAction,
    InvalidActionError,
    MarkMitigated,
    RecordAction,
    Reopen,
    Resolve,
)

AUTHOR_COOKIE = "grolla_author"
AUTHOR_COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 dias
AUTHOR_MAX_LENGTH = 60

STATE_LABELS = {
    IncidentState.DETECTED: "Detectado",
    IncidentState.INFORMED: "Informado",
    IncidentState.HANDLING: "Em tratamento",
    IncidentState.MITIGATED: "Mitigado",
    IncidentState.RESOLVED: "Resolvido",
    IncidentState.DISCARDED: "Descartado",
}

_ALWAYS = ("severity", "record", "discard-hypothesis", "resolve", "discard")

# Só esconde os botões que não fazem sentido; quem valida a ação é o domínio.
ALLOWED_ACTIONS: Mapping[IncidentState, frozenset[str]] = {
    IncidentState.DETECTED: frozenset({"assume", *_ALWAYS}),
    IncidentState.INFORMED: frozenset({"assume", *_ALWAYS}),
    # Outra pessoa pode assumir como IC a qualquer momento; o domínio recusa o próprio IC.
    IncidentState.HANDLING: frozenset({"assume", "mitigate", *_ALWAYS}),
    IncidentState.MITIGATED: frozenset({"assume", "reopen", *_ALWAYS}),
    IncidentState.RESOLVED: frozenset(),
    IncidentState.DISCARDED: frozenset(),
}

SEVERITIES = tuple(Severity)


def _severity(form: Mapping[str, str]) -> ChangeSeverity:
    try:
        return ChangeSeverity(Severity(form.get("severidade", "")))
    except ValueError as error:
        raise InvalidActionError("severidade inválida") from error


# Nome na rota → ação montada a partir dos campos do form.
_ACTIONS: Mapping[str, Callable[[Mapping[str, str]], HumanAction]] = {
    "assume": lambda _: AssumeCommander(),
    "severity": _severity,
    "record": lambda form: RecordAction(form.get("texto", "")),
    "discard-hypothesis": lambda form: DiscardHypothesis(
        form.get("titulo", ""), form.get("motivo", "")
    ),
    "mitigate": lambda _: MarkMitigated(),
    "reopen": lambda _: Reopen(),
    "resolve": lambda form: Resolve(form.get("motivo", "")),
    "discard": lambda form: DiscardIncident(form.get("motivo", "")),
}


def action_from(name: str, form: Mapping[str, str]) -> HumanAction | None:
    """A ação do form; None se o nome não é de uma ação; InvalidActionError se o form é inválido."""
    build = _ACTIONS.get(name)
    return None if build is None else build(form)


def parse_form(body: bytes) -> dict[str, str]:
    """Form urlencoded do HTMX: o primeiro valor de cada campo, sem espaços nas pontas."""
    fields = parse_qs(body.decode(), keep_blank_values=True)
    return {name: values[0].strip() for name, values in fields.items()}


def encode_author(name: str) -> str:
    return quote(name)


def decode_author(cookie: str | None) -> str | None:
    """O nome guardado no cookie; None se não há nome."""
    name = unquote(cookie or "").strip()[:AUTHOR_MAX_LENGTH]
    return name or None


def safe_redirect(target: str) -> str:
    """Só redireciona para um caminho do próprio site."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/incidents"


def format_duration(delta: timedelta | None) -> str:
    """'4 min' ou '1 h 05 min'; vazio enquanto não há horário."""
    if delta is None:
        return ""
    minutes = max(0, int(delta.total_seconds() // 60))
    hours, rest = divmod(minutes, 60)
    return f"{minutes} min" if hours == 0 else f"{hours} h {rest:02d} min"


def _since(start: datetime, end: datetime | None) -> timedelta | None:
    return None if end is None else end - start


@dataclass(frozen=True)
class Header:
    state: str
    duration: str
    tta: str
    ttm: str
    ttr: str
    actions: frozenset[str]


def header_of(incident: Incident, now: datetime, author: str | None = None) -> Header:
    """Duração até agora, ou até o encerramento; TTA, TTM e TTR a partir da detecção.

    "Assumir" some só para quem já é o IC: outra pessoa pode assumir a qualquer momento.
    """
    detected = incident.detected_at
    actions = ALLOWED_ACTIONS[incident.state]
    if incident.commander is not None and incident.commander == author:
        actions = actions - {"assume"}
    return Header(
        state=STATE_LABELS[incident.state],
        duration=format_duration((incident.resolved_at or now) - detected),
        tta=format_duration(_since(detected, incident.handling_at)),
        ttm=format_duration(_since(detected, incident.mitigated_at)),
        ttr=format_duration(_since(detected, incident.resolved_at)),
        actions=actions,
    )


def is_agent(message: ChatMessage) -> bool:
    return message.author == AGENT_AUTHOR


def is_waiting(message: ChatMessage) -> bool:
    return message.reply_status in (ReplyStatus.PENDING, ReplyStatus.RUNNING)


def chat_version(messages: Sequence[ChatMessage]) -> str:
    """Muda quando chega mensagem ou uma pergunta deixa de esperar: o polling só troca o chat aí."""
    waiting = sum(1 for message in messages if is_waiting(message))
    return f"{len(messages)}-{waiting}"
