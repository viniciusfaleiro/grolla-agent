"""Post mortem: o documento blameless de um incidente resolvido, com seções versionadas, plano de
ação e ciclo de vida (rascunho → em revisão → aprovado → encerrado).

Métricas e timeline são montadas aqui a partir do log de eventos, sem LLM; as demais seções são
escritas pelo modelo e editadas pelo time. As mensagens de InvalidPostMortemError, em português,
são mostradas ao time.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from grolla.domain.incidents import (
    SYSTEM_AUTHOR,
    Alert,
    EventType,
    Incident,
    IncidentEvent,
    duration_text,
)

AGENT_AUTHOR = "Grolla"


class SectionKey(StrEnum):  # na ordem do template
    SUMMARY = "resumo"
    IMPACT = "impacto"
    METRICS = "metricas"  # determinística
    TIMELINE = "timeline"  # determinística
    DETECTION = "deteccao"
    FACTORS = "fatores"
    HYPOTHESES = "hipoteses"
    LESSONS = "licoes"  # o que funcionou, o que não funcionou e onde tivemos sorte
    ACTIONS = "plano"  # texto de apoio; os itens ficam em ActionItem
    AGENT = "avaliacao_agente"


DETERMINISTIC = frozenset({SectionKey.METRICS, SectionKey.TIMELINE})

SECTION_TITLES: Mapping[SectionKey, str] = {
    SectionKey.SUMMARY: "Resumo executivo",
    SectionKey.IMPACT: "Impacto",
    SectionKey.METRICS: "Métricas do incidente",
    SectionKey.TIMELINE: "Timeline",
    SectionKey.DETECTION: "Detecção",
    SectionKey.FACTORS: "Fatores contribuintes",
    SectionKey.HYPOTHESES: "Hipóteses investigadas",
    SectionKey.LESSONS: "O que funcionou, o que não funcionou e onde tivemos sorte",
    SectionKey.ACTIONS: "Plano de ação",
    SectionKey.AGENT: "Avaliação do agente",
}


class PostMortemStatus(StrEnum):
    DRAFT = "rascunho"
    REVIEW = "em_revisao"
    APPROVED = "aprovado"
    CLOSED = "encerrado"


class ActionKind(StrEnum):
    PREVENT = "prevenir"
    DETECT = "detectar"
    MITIGATE = "mitigar"


class ActionStatus(StrEnum):
    OPEN = "aberto"
    DONE = "concluido"
    ACCEPTED = "aceito"


@dataclass(frozen=True)
class Section:
    key: SectionKey
    body: str  # markdown
    author: str  # AGENT_AUTHOR ou a pessoa
    updated_at: datetime


@dataclass(frozen=True)
class ActionItem:
    id: UUID
    description: str
    kind: ActionKind
    owner: str | None = None
    due: date | None = None
    status: ActionStatus = ActionStatus.OPEN


@dataclass(frozen=True)
class AgentEvaluation:
    """O que o time responde sobre o agente no post mortem; base das métricas de acerto."""

    main_hypothesis_correct: bool | None = None
    minutes_to_right: int | None = None
    memory_helped: bool | None = None
    author: str | None = None
    at: datetime | None = None


@dataclass(frozen=True)
class PostMortem:
    incident_id: UUID
    status: PostMortemStatus
    sections: tuple[Section, ...]  # na ordem do template, todas as chaves
    actions: tuple[ActionItem, ...]
    created_at: datetime
    updated_at: datetime
    status_by: str = AGENT_AUTHOR  # quem moveu para o status atual
    status_at: datetime | None = None
    evaluation: AgentEvaluation = field(default_factory=AgentEvaluation)


@dataclass(frozen=True)
class SectionVersion:
    """O texto de uma seção ANTES de uma mudança."""

    incident_id: UUID
    key: SectionKey
    at: datetime
    author: str
    reason: str
    body: str


@dataclass(frozen=True)
class PostMortemDraft:
    """O que o modelo devolve no rascunho, já validado."""

    sections: Mapping[SectionKey, str]  # sem as determinísticas
    actions: tuple[tuple[str, ActionKind], ...]  # descrição e tipo, sem dono nem prazo


class InvalidPostMortemError(ValueError):
    """Pedido recusado: transição de status inválida, aprovar sem dono, seção desconhecida."""


NOT_GENERATED = "não gerada"  # prefixo das seções sem texto do modelo: "não gerada: <motivo>"
NO_TEXT = "o modelo não escreveu a seção"
NONE = "—"  # métrica que não aconteceu (ninguém assumiu, não houve mitigação)
EDIT_REASON = "editada na página"
REGENERATE_REASON = "regenerada pelo agente"

STATUS_LABELS: Mapping[PostMortemStatus, str] = {
    PostMortemStatus.DRAFT: "rascunho",
    PostMortemStatus.REVIEW: "em revisão",
    PostMortemStatus.APPROVED: "aprovado",
    PostMortemStatus.CLOSED: "encerrado",
}

# Rascunho → em revisão → aprovado → encerrado; aprovado volta a em revisão para corrigir.
TRANSITIONS: Mapping[PostMortemStatus, frozenset[PostMortemStatus]] = {
    PostMortemStatus.DRAFT: frozenset({PostMortemStatus.REVIEW}),
    PostMortemStatus.REVIEW: frozenset({PostMortemStatus.APPROVED}),
    PostMortemStatus.APPROVED: frozenset({PostMortemStatus.CLOSED, PostMortemStatus.REVIEW}),
    PostMortemStatus.CLOSED: frozenset(),
}
LOCKED = frozenset({PostMortemStatus.APPROVED, PostMortemStatus.CLOSED})  # seções não mudam
FINISHED = frozenset({ActionStatus.DONE, ActionStatus.ACCEPTED})

AUTHOR_REQUIRED = "informe o seu nome antes de agir"
BODY_REQUIRED = "escreva o texto da seção"
DESCRIPTION_REQUIRED = "descreva o item do plano"
UNKNOWN_ACTION = "o item do plano não existe"
APPROVED_LOCKED = "post mortem aprovado não pode ser alterado; volte para em revisão para corrigir"
CLOSED_LOCKED = "post mortem encerrado não pode ser alterado"
APPROVE_NEEDS = "para aprovar, todo item do plano precisa de dono e prazo; falta em: "
CLOSE_NEEDS = "para encerrar, todo item do plano precisa estar concluído ou aceito; falta em: "


def _duration(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return NONE
    return duration_text(max(end - start, timedelta(0)))


def metrics_text(incident: Incident, alerts: Sequence[Alert]) -> str:
    """TTD (do início do primeiro alerta à detecção), TTA, TTM e TTR, em markdown."""
    first = min((a.starts_at for a in alerts), default=None)
    detected = incident.detected_at
    return "\n".join(
        [
            f"- **TTD** (do primeiro alerta à detecção): {_duration(first, detected)}",
            f"- **TTA** (da detecção a alguém assumir): "
            f"{_duration(detected, incident.handling_at)}",
            f"- **TTM** (da detecção à mitigação): {_duration(detected, incident.mitigated_at)}",
            f"- **TTR** (da detecção ao encerramento): {_duration(detected, incident.resolved_at)}",
        ]
    )


ALERT_ORIGIN = "alerta"
AGENT_ORIGIN = "agente"
CHAT_ORIGIN = "chat"
PERSON_ORIGIN = "pessoa"

_ALERT_EVENTS = frozenset(
    {
        EventType.ALERT_RECEIVED,
        EventType.ALERT_GROUPED,
        EventType.ALERT_RESOLVED,
        EventType.INCIDENT_NORMALIZED,
    }
)
_AGENT_EVENTS = frozenset(
    {
        EventType.ANALYSIS_REQUESTED,
        EventType.ANALYSIS_COMPLETED,
        EventType.ANALYSIS_PARTIAL,
        EventType.VERIFICATION_REQUESTED,
        EventType.VERIFICATION_COMPLETED,
        EventType.VERIFICATION_PARTIAL,
        EventType.CYCLE_COMPLETED,
        EventType.CYCLE_PARTIAL,
        EventType.FOLLOW_UP_STOPPED,
        EventType.AGENT_MESSAGE,
        EventType.AGENT_PROPOSAL,
    }
)
_HIDDEN_EVENTS = frozenset(
    {
        EventType.NOTIFICATION_QUEUED,
        EventType.NOTIFICATION_DELIVERED,
        EventType.NOTIFICATION_FAILED,
    }
)
EVENT_TEXT: Mapping[EventType, str] = {
    EventType.ALERT_RECEIVED: "alerta disparou",
    EventType.ALERT_GROUPED: "alerta agrupado no incidente",
    EventType.ALERT_RESOLVED: "alerta resolvido",
    EventType.SEVERITY_CHANGED: "severidade mudou",
    EventType.STATE_CHANGED: "estado mudou",
    EventType.INCIDENT_NORMALIZED: "todos os alertas normalizaram",
    EventType.ANALYSIS_REQUESTED: "começou a investigar",
    EventType.ANALYSIS_COMPLETED: "análise inicial pronta",
    EventType.ANALYSIS_PARTIAL: "análise inicial parcial",
    EventType.VERIFICATION_REQUESTED: "começou a validar as hipóteses",
    EventType.VERIFICATION_COMPLETED: "validação pronta",
    EventType.VERIFICATION_PARTIAL: "validação parcial",
    EventType.COMMANDER_ASSIGNED: "assumiu como IC",
    EventType.ACTION_RECORDED: "registrou uma ação",
    EventType.HYPOTHESIS_DISCARDED: "descartou uma hipótese",
    EventType.INCIDENT_MITIGATED: "marcou como mitigado",
    EventType.INCIDENT_REOPENED: "reabriu",
    EventType.INCIDENT_RESOLVED: "encerrou o incidente",
    EventType.INCIDENT_DISCARDED: "descartou o incidente",
    EventType.HUMAN_MESSAGE: "escreveu",
    EventType.AGENT_MESSAGE: "respondeu no chat",
    EventType.CYCLE_COMPLETED: "ciclo de acompanhamento",
    EventType.CYCLE_PARTIAL: "ciclo de acompanhamento parcial",
    EventType.FOLLOW_UP_STOPPED: "parou o acompanhamento",
    EventType.AGENT_PROPOSAL: "sugeriu",
}
_DETAIL_KEYS = ("rule_name", "summary", "root_cause", "title", "text", "proposal", "reason")
DETAIL_LIMIT = 200  # caracteres de cada detalhe de um evento na timeline


def origin_of(event: IncidentEvent) -> str | None:
    """Alerta, agente, chat ou pessoa; None para os eventos internos de entrega de cards.

    O que o núcleo grava sozinho a partir dos alertas (severidade e estado) conta como alerta.
    """
    if event.type in _HIDDEN_EVENTS:
        return None
    if event.type in _ALERT_EVENTS:
        return ALERT_ORIGIN
    if event.type in _AGENT_EVENTS:
        return AGENT_ORIGIN
    if event.type is EventType.HUMAN_MESSAGE:
        return CHAT_ORIGIN
    return ALERT_ORIGIN if event.author == SYSTEM_AUTHOR else PERSON_ORIGIN


def _detail(data: Mapping[str, Any]) -> str:
    parts = [" ".join(str(data[k]).split())[:DETAIL_LIMIT] for k in _DETAIL_KEYS if data.get(k)]
    if data.get("from") and data.get("to"):
        parts.insert(0, f"{data['from']} → {data['to']}")
    return f": {' · '.join(parts)}" if parts else ""


def timeline_text(events: Sequence[IncidentEvent]) -> str:
    """Uma linha por evento relevante, com horário, origem (alerta, agente, chat ou pessoa) e o
    que aconteceu; sem os eventos internos de entrega de cards.
    """
    lines: list[str] = []
    for event in events:
        origin = origin_of(event)
        if origin is None:
            continue
        who = f"{event.author}: " if origin in (CHAT_ORIGIN, PERSON_ORIGIN) else ""
        what = EVENT_TEXT.get(event.type, event.type.value)
        lines.append(f"- {event.at:%H:%M} [{origin}] {who}{what}{_detail(event.data)}")
    return "\n".join(lines) or "- nenhum evento registrado"


def drafted(  # noqa: PLR0913 — o rascunho reúne o incidente, o log e o texto do modelo
    incident: Incident,
    alerts: Sequence[Alert],
    events: Sequence[IncidentEvent],
    *,
    draft: PostMortemDraft | None,
    failure: str | None,
    now: datetime,
) -> PostMortem:
    """O rascunho: seções determinísticas do log e as do modelo; sem `draft`, as do modelo dizem
    "não gerada: <failure>".
    """
    written: Mapping[SectionKey, str] = {} if draft is None else draft.sections
    missing = f"{NOT_GENERATED}: {failure or NO_TEXT}"

    def body(key: SectionKey) -> str:
        if key is SectionKey.METRICS:
            return metrics_text(incident, alerts)
        if key is SectionKey.TIMELINE:
            return timeline_text(events)
        return written.get(key, "").strip() or missing

    proposed = () if draft is None else draft.actions
    return PostMortem(
        incident_id=incident.id,
        status=PostMortemStatus.DRAFT,
        sections=tuple(Section(key, body(key), AGENT_AUTHOR, now) for key in SectionKey),
        actions=tuple(
            ActionItem(uuid4(), text.strip(), kind) for text, kind in proposed if text.strip()
        ),
        created_at=now,
        updated_at=now,
        status_by=AGENT_AUTHOR,
        status_at=now,
    )


def _required(text: str, message: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise InvalidPostMortemError(message)
    return stripped


def _unlocked(pm: PostMortem) -> None:
    if pm.status is PostMortemStatus.APPROVED:
        raise InvalidPostMortemError(APPROVED_LOCKED)
    if pm.status is PostMortemStatus.CLOSED:
        raise InvalidPostMortemError(CLOSED_LOCKED)


def check_regenerable(pm: PostMortem) -> None:
    """Levanta InvalidPostMortemError em post mortem aprovado ou encerrado."""
    _unlocked(pm)


def _touched(pm: PostMortem, author: str, now: datetime) -> PostMortem:
    """A mudança de uma pessoa: rascunho passa a em revisão."""
    if pm.status is PostMortemStatus.DRAFT:
        return replace(
            pm, status=PostMortemStatus.REVIEW, status_by=author, status_at=now, updated_at=now
        )
    return replace(pm, updated_at=now)


def _rewritten(  # noqa: PLR0913 — a reescrita precisa de qual, o quê, quem, por quê e quando
    pm: PostMortem, key: SectionKey, body: str, author: str, *, reason: str, now: datetime
) -> tuple[PostMortem, SectionVersion]:
    previous = next(s for s in pm.sections if s.key is key)
    sections = tuple(Section(key, body, author, now) if s.key is key else s for s in pm.sections)
    version = SectionVersion(pm.incident_id, key, now, author, reason, previous.body)
    return replace(pm, sections=sections, updated_at=now), version


def edited(
    pm: PostMortem, key: SectionKey, body: str, author: str, now: datetime
) -> tuple[PostMortem, SectionVersion]:
    """Seção editada por uma pessoa; rascunho passa a em revisão. Levanta InvalidPostMortemError
    em post mortem aprovado ou encerrado.
    """
    author = _required(author, AUTHOR_REQUIRED)
    body = _required(body, BODY_REQUIRED)
    _unlocked(pm)
    updated, version = _rewritten(pm, key, body, author, reason=EDIT_REASON, now=now)
    return _touched(updated, author, now), version


def regenerated(
    pm: PostMortem, key: SectionKey, body: str, now: datetime
) -> tuple[PostMortem, SectionVersion]:
    """Seção reescrita pelo agente; o status não muda. Levanta InvalidPostMortemError em post
    mortem aprovado ou encerrado.
    """
    _unlocked(pm)
    text = body.strip() or NO_TEXT
    return _rewritten(pm, key, text, AGENT_AUTHOR, reason=REGENERATE_REASON, now=now)


def _names(items: Sequence[ActionItem]) -> str:
    return ", ".join(f'"{a.description}"' for a in items)


def _approvable(actions: Sequence[ActionItem]) -> None:
    missing = [a for a in actions if not a.owner or a.due is None]
    if missing:
        raise InvalidPostMortemError(APPROVE_NEEDS + _names(missing))


def moved(pm: PostMortem, to: PostMortemStatus, author: str, now: datetime) -> PostMortem:
    """Muda o status seguindo o ciclo de vida; aprovar exige dono e prazo em todo item, encerrar
    exige todo item concluído ou aceito, e aprovado pode voltar a em revisão. Levanta
    InvalidPostMortemError.
    """
    author = _required(author, AUTHOR_REQUIRED)
    if to is pm.status:
        raise InvalidPostMortemError(f"o post mortem já está {STATUS_LABELS[to]}")
    if to not in TRANSITIONS[pm.status]:
        raise InvalidPostMortemError(
            f"não dá para passar de {STATUS_LABELS[pm.status]} para {STATUS_LABELS[to]}"
        )
    if to is PostMortemStatus.APPROVED:
        _approvable(pm.actions)
    if to is PostMortemStatus.CLOSED:
        open_items = [a for a in pm.actions if a.status not in FINISHED]
        if open_items:
            raise InvalidPostMortemError(CLOSE_NEEDS + _names(open_items))
    return replace(pm, status=to, status_by=author, status_at=now, updated_at=now)


def action_changed(pm: PostMortem, item: ActionItem, author: str, now: datetime) -> PostMortem:
    """Item do plano novo (id ainda não visto) ou alterado; rascunho passa a em revisão.

    Encerrado não muda; num aprovado, todo item continua precisando de dono e prazo. Levanta
    InvalidPostMortemError.
    """
    author = _required(author, AUTHOR_REQUIRED)
    item = replace(
        item,
        description=_required(item.description, DESCRIPTION_REQUIRED),
        owner=(item.owner or "").strip() or None,
    )
    if pm.status is PostMortemStatus.CLOSED:
        raise InvalidPostMortemError(CLOSED_LOCKED)
    known = any(a.id == item.id for a in pm.actions)
    actions = (
        tuple(item if a.id == item.id else a for a in pm.actions) if known else (*pm.actions, item)
    )
    if pm.status is PostMortemStatus.APPROVED:
        _approvable(actions)
    return _touched(replace(pm, actions=actions), author, now)


def overdue(pm: PostMortem, today: date) -> tuple[ActionItem, ...]:
    """Itens abertos com prazo antes de `today`."""
    return tuple(
        a
        for a in pm.actions
        if a.status is ActionStatus.OPEN and a.due is not None and a.due < today
    )


def evaluated(
    pm: PostMortem, evaluation: AgentEvaluation, author: str, now: datetime
) -> PostMortem:
    """A avaliação do agente preenchida por uma pessoa; levanta InvalidPostMortemError em post
    mortem encerrado, sem autor ou com minutos negativos.

    Contrato das métricas do agente: o corpo é do grolla-dominio.
    """
    raise NotImplementedError
