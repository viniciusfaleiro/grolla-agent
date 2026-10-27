"""Apresentação do war room: rótulos, durações, ações por estado e leitura dos forms."""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from urllib.parse import parse_qs, quote, unquote
from uuid import UUID

from grolla.domain.analysis import PARTIAL_REASON_TEXT, PartialReason
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage, ReplyStatus
from grolla.domain.follow_up import Cycle, CycleStatus, Proposal, Trend
from grolla.domain.incidents import (
    OPEN_STATES,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
    Severity,
)
from grolla.domain.transcript import TRANSCRIPT_ORIGIN, HypothesisVerdict, TranscriptItemKind
from grolla.domain.triage import AGENT_SEVERITY_REASON
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
    "cloud_health": "Nuvem",
    "cloud_quotas": "Nuvem",
    "cluster_state": "Cluster",
    "cluster_changes": "Cluster",
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
OPENED_FROM_CONSOLE = "Aberto pelo console"

TREND_LABELS = {
    Trend.WORSE: "piorando",
    Trend.STABLE: "estável",
    Trend.BETTER: "melhorando",
    Trend.NORMAL: "normalizado",
}

PROPOSAL_LABELS = {
    Proposal.MITIGATE: "Grolla sugere marcar como mitigado",
    Proposal.RESOLVE: "Grolla sugere encerrar",
}

# A proposta leva ao botão que já existe: mitigar é um form; encerrar abre o popover do motivo.
PROPOSAL_ACTIONS = {Proposal.MITIGATE: "mitigate", Proposal.RESOLVE: "resolve"}

RECENT_CYCLES = 6

AGENT_SEVERITY_LABEL = "Severidade sugerida pelo agente"

# Evento da junção → campo do data com o outro incidente; o rótulo termina com o link para ele.
LINKED_INCIDENT = {
    EventType.MERGE_SUGGESTED: "other",
    EventType.INCIDENT_MERGED: "merged",
    EventType.MERGED_INTO: "into",
}

MERGE_CANDIDATES = 10  # os abertos mais recentes no seletor "Juntar com…"


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


class AlarmKind(StrEnum):
    NORMALIZED = "normalizado"
    RETURNED = "voltou"


@dataclass(frozen=True)
class Alarm:
    kind: AlarmKind
    at: datetime  # da última normalização, ou do alerta que voltou


_ALERT_ARRIVED = frozenset({EventType.ALERT_RECEIVED, EventType.ALERT_GROUPED})


def alarm_of(incident: Incident, events: Sequence[IncidentEvent]) -> Alarm | None:
    """Faixa do alarme: normalizado (nada disparando) ou voltou (disparou depois de normalizar).

    Sem faixa no incidente encerrado, no que nunca teve alerta e enquanto dispara sem nunca ter
    normalizado.
    """
    if incident.state not in OPEN_STATES:
        return None
    normalized = [e.at for e in events if e.type is EventType.INCIDENT_NORMALIZED]
    if not normalized or not any(e.type in _ALERT_ARRIVED for e in events):
        return None
    last = max(normalized)
    if not incident.firing:
        return Alarm(AlarmKind.NORMALIZED, last)
    returned = [e.at for e in events if e.type in _ALERT_ARRIVED and e.at > last]
    return Alarm(AlarmKind.RETURNED, min(returned)) if returned else None


def alarm_normalized(incident: Incident) -> bool:
    """Selo da lista: aberto, teve alerta (o resolvido avança `last_alert_at`) e nada disparando."""
    return (
        incident.state in OPEN_STATES
        and not incident.firing
        and incident.last_alert_at > incident.detected_at
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


def trend_label(value: object) -> str:
    try:
        return TREND_LABELS[Trend(str(value))]
    except ValueError:
        return str(value)


def partial_text(value: object) -> str:
    try:
        return PARTIAL_REASON_TEXT[PartialReason(str(value))]
    except ValueError:
        return str(value)


def percent(rate: float | None) -> str:
    """'12,5%' ou '0%'; vazio quando a taxa não foi medida."""
    if rate is None:
        return ""
    return f"{rate:.1f}".removesuffix(".0").replace(".", ",") + "%"


def _proposal(value: object) -> Proposal | None:
    try:
        return Proposal(str(value))
    except ValueError:
        return None


def event_label(event: IncidentEvent, labels: Mapping[EventType, str]) -> str:
    """O rótulo do evento; o do ciclo já diz a tendência e o resumo."""
    build = _TRANSCRIPT_LABELS.get(event.type)
    return build(event) if build else _event_label(event, labels)


def _event_label(event: IncidentEvent, labels: Mapping[EventType, str]) -> str:
    data = event.data
    if event.type is EventType.CYCLE_COMPLETED:
        summary = plain_text(str(data.get("summary", "")))
        return f"Acompanhamento: {trend_label(data.get('trend'))} — {summary}".removesuffix(" — ")
    if event.type is EventType.SEEN_BEFORE_ANSWERED:
        return f"Já vi isso: {_seen_answer(event)}"
    proposal = _proposal(data.get("proposal"))
    if event.type is EventType.AGENT_PROPOSAL and proposal is not None:
        return PROPOSAL_LABELS[proposal]
    if event.type is EventType.SEVERITY_CHANGED and str(data.get("reason", "")).startswith(
        AGENT_SEVERITY_REASON
    ):
        return AGENT_SEVERITY_LABEL
    if event.type is EventType.INCIDENT_OPENED_MANUALLY and data.get("origin") == "console":
        return OPENED_FROM_CONSOLE
    return labels.get(event.type, event.type)


def _seen_answer(event: IncidentEvent) -> str:
    return "era o mesmo problema" if event.data.get("same") is True else "não era o mesmo problema"


def seen_before_answer(events: Iterable[IncidentEvent], past: UUID) -> IncidentEvent | None:
    """A última resposta ao "já vi isso" sobre o incidente `past`: a nova substitui a anterior."""
    answers = [
        event
        for event in events
        if event.type is EventType.SEEN_BEFORE_ANSWERED and str(event.data.get("past")) == str(past)
    ]
    return answers[-1] if answers else None


def seen_answer_text(event: IncidentEvent) -> str:
    """'Ana confirmou: era o mesmo problema' ou 'Ana respondeu: não era o mesmo problema'."""
    verb = "confirmou" if event.data.get("same") is True else "respondeu"
    return f"{display_name(event.author)} {verb}: {_seen_answer(event)}"


def same_problem(form: Mapping[str, str]) -> bool:
    """O `mesmo=sim|nao` dos botões do "já vi isso"."""
    answer = form.get("mesmo", "")
    if answer not in ("sim", "nao"):
        raise InvalidActionError("responda se era o mesmo problema")
    return answer == "sim"


def event_detail(event: IncidentEvent) -> str:
    """Uma linha legível com o que importa do evento; vazio se não há nada a dizer."""
    data = event.data
    transcript = _TRANSCRIPT_DETAILS.get(event.type)
    if transcript is not None:
        return transcript(data)
    if event.type is EventType.CYCLE_COMPLETED:
        return ""  # o rótulo já diz tudo
    if event.type is EventType.CYCLE_PARTIAL:
        reason = data.get("reason")
        return partial_text(reason) if reason else ""
    if "from" in data and "to" in data:
        change = f"{_label(data['from'])} → {_label(data['to'])}"
        reason = data.get("reason")
        return f"{change}: {reason}" if reason else change
    for key in ("text", "reason", "title", "commander", "summary"):
        value = data.get(key)
        if value:
            return plain_text(str(value))
    return ""


ITEM_LABELS = {
    TranscriptItemKind.DECISION: "Decisão",
    TranscriptItemKind.ACTION: "Ação",
    TranscriptItemKind.TIME: "Horário",
}
VERDICT_LABELS = {
    HypothesisVerdict.CONFIRMED: "A call confirmou",
    HypothesisVerdict.DISCARDED: "A call descartou",
}
# Depois do envio, qualquer um destes diz que a leitura terminou.
TRANSCRIPT_RESULTS = frozenset(
    {
        EventType.TRANSCRIPT_ITEM,
        EventType.TRANSCRIPT_PARTICIPANTS,
        EventType.TRANSCRIPT_HYPOTHESIS,
        EventType.TRANSCRIPT_FAILED,
    }
)

Data = Mapping[str, object]


def _item_label(data: Data) -> str:
    """'Decisão (transcrição): fazer o rollback' ou 'Ação (transcrição): … · Pedro'."""
    try:
        kind = ITEM_LABELS[TranscriptItemKind(str(data.get("kind")))]
    except ValueError:
        kind = "Item"
    owner = data.get("owner")
    label = f"{kind} ({TRANSCRIPT_ORIGIN}): {data.get('text', '')}"
    return f"{label} · {owner}" if owner else label


def _hypothesis_label(data: Data) -> str:
    try:
        verdict = VERDICT_LABELS[HypothesisVerdict(str(data.get("verdict")))]
    except ValueError:
        verdict = "A call falou da hipótese"
    return f"{verdict}: {data.get('hypothesis', '')}"


def _quoted(value: object) -> str:
    return f"“{value}”" if value else ""


def _received_detail(data: Data) -> str:
    chars = f"{data.get('chars', 0)} caracteres"
    return f"{chars}, cortada no limite" if data.get("truncated") is True else chars


def _item_detail(data: Data) -> str:
    said_at = data.get("said_at")
    parts = (f"às {said_at}" if said_at else "", _quoted(data.get("excerpt")))
    return " · ".join(part for part in parts if part)


def _participants(data: Data) -> str:
    names: object = data.get("names")
    if not isinstance(names, list | tuple):
        return ""
    return ", ".join(str(name) for name in names)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType] — vem do JSON do log


_TRANSCRIPT_LABELS: Mapping[EventType, Callable[[IncidentEvent], str]] = {
    EventType.TRANSCRIPT_ITEM: lambda event: _item_label(event.data),
    EventType.TRANSCRIPT_HYPOTHESIS: lambda event: _hypothesis_label(event.data),
    EventType.TRANSCRIPT_FAILED: lambda event: (
        f"A transcrição falhou: {event.data.get('reason', '')}"
    ),
    # O autor entra no rótulo ("Transcrição enviada por Ana"): a timeline não repete o "por".
    EventType.TRANSCRIPT_RECEIVED: lambda event: (
        f"Transcrição enviada por {display_name(event.author)}"
    ),
}

_TRANSCRIPT_DETAILS: Mapping[EventType, Callable[[Data], str]] = {
    EventType.TRANSCRIPT_RECEIVED: _received_detail,
    EventType.TRANSCRIPT_ITEM: _item_detail,
    EventType.TRANSCRIPT_PARTICIPANTS: _participants,
    EventType.TRANSCRIPT_HYPOTHESIS: lambda data: _quoted(data.get("excerpt")),
    EventType.TRANSCRIPT_FAILED: lambda _: "",  # o motivo já está no rótulo
}


def from_transcript(event: IncidentEvent) -> bool:
    return event.data.get("origin") == TRANSCRIPT_ORIGIN


def failed_transcript(event: IncidentEvent) -> int | None:
    """O id da transcrição que falhou, para o botão "Reprocessar"; None nos demais eventos."""
    if event.type is not EventType.TRANSCRIPT_FAILED:
        return None
    value = event.data.get("transcript")
    return value if isinstance(value, int) else None


def reading_transcript(events: Iterable[IncidentEvent]) -> bool:
    """A última transcrição enviada ainda não tem itens nem falha: o Grolla está lendo.

    Os itens não levam o id da transcrição: vale o que veio depois do último envio.
    """
    reading = False
    for event in events:
        if event.type is EventType.TRANSCRIPT_RECEIVED:
            reading = True
        elif event.type in TRANSCRIPT_RESULTS:
            reading = False
    return reading


TRANSCRIPT_EXTENSIONS = (".vtt", ".srt", ".txt")
UTF8_MAX_BYTES = 4  # um caractere ocupa até 4 bytes em UTF-8


def transcript_file(filename: str, content: bytes, max_chars: int) -> str:
    """O texto do arquivo enviado; InvalidActionError se não é .vtt/.srt/.txt, se não é UTF-8
    ou se não cabe no limite nem antes da limpeza (o corte fino é do caso de uso)."""
    if not filename.lower().endswith(TRANSCRIPT_EXTENSIONS):
        raise InvalidActionError("envie um arquivo .vtt, .srt ou .txt")
    if len(content) > max_chars * UTF8_MAX_BYTES:
        raise InvalidActionError(f"o arquivo passa do limite de {max_chars} caracteres")
    try:
        return content.decode("utf-8-sig")  # o .vtt do Teams costuma vir com BOM
    except UnicodeDecodeError as error:
        raise InvalidActionError("o arquivo precisa ser texto em UTF-8") from error


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
    """Markdown mínimo e seguro para o texto do agente: títulos, parágrafos, listas, negrito,
    links.
    """
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
        heading = line.lstrip("#")
        if line.startswith("#") and heading.startswith(" "):  # "## Seção" dos dossiês
            flush()
            blocks.append(f'<p class="rich-heading">{_inline(heading.strip())}</p>')
        elif line.startswith(_BULLET):
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


@dataclass(frozen=True)
class ProposalNotice:
    label: str  # "Grolla sugere marcar como mitigado"
    reason: str
    action: str  # o nome da ação no war room


def current_proposal(incident: Incident, events: Sequence[IncidentEvent]) -> ProposalNotice | None:
    """A última proposta do agente, enquanto o incidente segue no estado em que ela foi feita."""
    last = next((e for e in reversed(events) if e.type is EventType.AGENT_PROPOSAL), None)
    if last is None or last.data.get("state") != incident.state.value:
        return None
    proposal = _proposal(last.data.get("proposal"))
    if proposal is None:
        return None
    return ProposalNotice(
        label=PROPOSAL_LABELS[proposal],
        reason=str(last.data.get("reason", "")),
        action=PROPOSAL_ACTIONS[proposal],
    )


def recent_cycles(cycles: Sequence[Cycle]) -> list[Cycle]:
    """Os últimos ciclos terminados, o mais recente primeiro."""
    done = [c for c in cycles if c.status in (CycleStatus.COMPLETED, CycleStatus.PARTIAL)]
    return done[::-1][:RECENT_CYCLES]


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


def linked_incident(event: IncidentEvent) -> UUID | None:
    """O outro incidente citado num evento da junção; None nos demais."""
    key = LINKED_INCIDENT.get(event.type)
    if key is None:
        return None
    try:
        return UUID(str(event.data.get(key)))
    except ValueError:
        return None


def linked_ids(events: Iterable[IncidentEvent]) -> set[UUID]:
    return {ref for event in events if (ref := linked_incident(event)) is not None}


def last_event(events: Sequence[IncidentEvent], kind: EventType) -> IncidentEvent | None:
    return next((event for event in reversed(events) if event.type is kind), None)


@dataclass(frozen=True)
class MergeNotice:
    other: Incident
    reason: str


def merge_suggestion(
    incident: Incident, events: Sequence[IncidentEvent], linked: Mapping[UUID, Incident]
) -> MergeNotice | None:
    """A última sugestão de junção, enquanto os dois incidentes seguem abertos."""
    last = last_event(events, EventType.MERGE_SUGGESTED)
    ref = None if last is None else linked_incident(last)
    other = None if ref is None else linked.get(ref)
    if last is None or other is None or incident.state not in OPEN_STATES:
        return None
    if other.state not in OPEN_STATES:
        return None
    return MergeNotice(other=other, reason=str(last.data.get("reason", "")))


def merged_into(
    events: Sequence[IncidentEvent], linked: Mapping[UUID, Incident]
) -> Incident | None:
    """O principal em que este incidente foi juntado."""
    last = last_event(events, EventType.MERGED_INTO)
    ref = None if last is None else linked_incident(last)
    return None if ref is None else linked.get(ref)


def merge_candidates(incident: Incident, recent: Iterable[Incident]) -> list[Incident]:
    """Os outros incidentes abertos, os mais recentes primeiro."""
    others = [i for i in recent if i.id != incident.id and i.state in OPEN_STATES]
    return others[:MERGE_CANDIDATES]


def merge_target(form: Mapping[str, str]) -> UUID:
    """O `destino` do form de juntar."""
    try:
        return UUID(form.get("destino", ""))
    except ValueError as error:
        raise InvalidActionError("escolha o incidente em que este será juntado") from error
