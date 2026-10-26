"""Apresentação do war room: rótulos, durações, ações por estado e leitura dos forms."""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qs, quote, unquote

from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState, Severity
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
from markupsafe import Markup, escape

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

AGENT_NAME = "Grolla"  # o agente aparece com o nome e o avatar do mascote

# Ciclo mostrado no cabeçalho; Informado conta como a etapa Detectado.
STAGES = (
    ("Detectado", (IncidentState.DETECTED, IncidentState.INFORMED)),
    ("Em tratamento", (IncidentState.HANDLING,)),
    ("Mitigado", (IncidentState.MITIGATED,)),
    ("Resolvido", (IncidentState.RESOLVED,)),
)

# A ação em destaque em cada estado aberto: o próximo passo do ciclo.
PRIMARY_ACTION = {
    IncidentState.DETECTED: "assume",
    IncidentState.INFORMED: "assume",
    IncidentState.HANDLING: "mitigate",
    IncidentState.MITIGATED: "resolve",
}

TOOL_LABELS = {
    "query_metric": "Métrica",
    "search_logs": "Logs",
    "find_traces": "Traces",
    "recent_deploys": "Deploys",
    "cloud_resources": "Nuvem",
    "cloud_changes": "Nuvem",
    "cloud_alarms": "Nuvem",
}

# Enfileirar um card não interessa a quem conduz; a entrega aparece, discreta.
HIDDEN_EVENTS = frozenset({EventType.NOTIFICATION_QUEUED})
QUIET_EVENTS = frozenset({EventType.NOTIFICATION_DELIVERED})

SYSTEM_AUTHORS = frozenset({"sistema", AGENT_AUTHOR})

# Perguntas prontas no chat vazio: um clique envia.
SUGGESTIONS = (
    "Os erros continuam?",
    "O que mudou antes do incidente começar?",
    "Resuma o incidente para eu passar ao time",
)

FAILED_REPLY_PREFIX = "não consegui responder"


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
class Stage:
    label: str
    status: str  # "feita", "atual" ou "futura"


def stages_of(state: IncidentState) -> tuple[Stage, ...]:
    """O ciclo com a etapa atual marcada; vazio para incidente descartado."""
    if state is IncidentState.DISCARDED:
        return ()
    if state is IncidentState.RESOLVED:
        return tuple(Stage(label, "feita") for label, _ in STAGES)
    current = next(i for i, (_, states) in enumerate(STAGES) if state in states)
    status = ("feita", "atual", "futura")
    return tuple(
        Stage(label, status[(i > current) + (i >= current)]) for i, (label, _) in enumerate(STAGES)
    )


@dataclass(frozen=True)
class Header:
    state: str
    duration: str
    tta: str
    ttm: str
    ttr: str
    actions: frozenset[str]
    primary: str | None = None
    stages: tuple[Stage, ...] = ()
    detected_ago: str = ""


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
        primary=next((a for a in (PRIMARY_ACTION.get(incident.state),) if a in actions), None),
        stages=stages_of(incident.state),
        detected_ago=ago(now - detected),
    )


def ago(delta: timedelta) -> str:
    """'agora', 'há 4 min', 'há 1 h 05 min' ou 'há 3 dias'."""
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes == 0:
        return "agora"
    if minutes >= 48 * 60:
        return f"há {minutes // (24 * 60)} dias"
    return f"há {format_duration(delta)}"


def initials(name: str) -> str:
    parts = name.split()
    return "".join(part[0] for part in parts[:2]).upper() or "?"


def display_name(author: str) -> str:
    return AGENT_NAME if author == AGENT_AUTHOR else author


def discarded_hypotheses(events: Iterable[IncidentEvent]) -> dict[str, tuple[str, str]]:
    """Título da hipótese → (autor, motivo) do descarte."""
    return {
        str(event.data.get("title", "")): (event.author, str(event.data.get("reason", "")))
        for event in events
        if event.type is EventType.HYPOTHESIS_DISCARDED
    }


def visible_events(events: Iterable[IncidentEvent]) -> list[IncidentEvent]:
    return [event for event in events if event.type not in HIDDEN_EVENTS]


def _label(value: object) -> str:
    """Estado interno vira o rótulo em português; o resto fica como veio."""
    try:
        return STATE_LABELS[IncidentState(str(value))]
    except ValueError:
        return str(value)


def plain_text(text: str) -> str:
    """Sem as marcas de markdown: para resumos de uma linha."""
    return " ".join(text.replace("**", "").replace("`", "").split())


def is_quiet(event: IncidentEvent) -> bool:
    return event.type in QUIET_EVENTS


def event_detail(event: IncidentEvent) -> str:
    """Uma linha legível com o que importa do evento; vazio se não há nada a dizer."""
    data = event.data
    if "from" in data and "to" in data:
        return f"{_label(data['from'])} → {_label(data['to'])}"
    for key in ("text", "reason", "title", "commander", "summary"):
        value = data.get(key)
        if value:
            return plain_text(str(value))
    return ""


_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_URL = r"https?://(?:[^\s&<>()]|&amp;)+"
# Link em markdown "[texto](url)" ou URL solta.
_LINK = re.compile(rf"\[([^\]]+)\]\(({_URL})\)|https?://(?:[^\s&<>]|&amp;)+")
_TRAILING = ".,;:!?)"
_BULLET = ("- ", "* ", "• ")


def _link(match: re.Match[str]) -> str:
    if match.group(1):
        return f'<a href="{match.group(2)}" target="_blank" rel="noopener">{match.group(1)}</a>'
    url = match.group(0)
    tail = ""
    while url and url[-1] in _TRAILING:
        url, tail = url[:-1], url[-1] + tail
    return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>{tail}'


def _inline(text: str) -> str:
    html = str(escape(text))  # escapa antes: só as marcas abaixo viram HTML
    html = _CODE.sub(r"<code>\1</code>", html)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return _LINK.sub(_link, html)


def rich_text(text: str) -> Markup:
    """Markdown mínimo e seguro para as respostas do agente: parágrafos, listas, negrito, links."""
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(_BULLET):
            if paragraph:
                flush()
            items.append(_inline(line[2:].strip()))
        elif not line:
            flush()
        else:
            if items:
                flush()
            paragraph.append(_inline(line))
    flush()
    return Markup("".join(blocks))  # noqa: S704 — conteúdo escapado em _inline


def is_agent(message: ChatMessage) -> bool:
    return message.author == AGENT_AUTHOR


def is_failure(message: ChatMessage) -> bool:
    return message.text.startswith(FAILED_REPLY_PREFIX)


def is_waiting(message: ChatMessage) -> bool:
    return message.reply_status in (ReplyStatus.PENDING, ReplyStatus.RUNNING)


def chat_version(messages: Sequence[ChatMessage]) -> str:
    """Muda quando chega mensagem ou uma pergunta deixa de esperar: o polling só troca o chat aí."""
    waiting = sum(1 for message in messages if is_waiting(message))
    return f"{len(messages)}-{waiting}"
