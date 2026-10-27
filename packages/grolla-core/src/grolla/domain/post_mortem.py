"""Post mortem: o documento blameless de um incidente resolvido, com seções versionadas, plano de
ação e ciclo de vida (rascunho → em revisão → aprovado → encerrado).

Métricas e timeline são montadas aqui a partir do log de eventos, sem LLM; as demais seções são
escritas pelo modelo e editadas pelo time.

Contrato do post mortem: os corpos são do grolla-dominio.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from grolla.domain.incidents import Alert, Incident, IncidentEvent

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
class PostMortem:
    incident_id: UUID
    status: PostMortemStatus
    sections: tuple[Section, ...]  # na ordem do template, todas as chaves
    actions: tuple[ActionItem, ...]
    created_at: datetime
    updated_at: datetime
    status_by: str = AGENT_AUTHOR  # quem moveu para o status atual
    status_at: datetime | None = None


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


def metrics_text(incident: Incident, alerts: Sequence[Alert]) -> str:
    """TTD (do início do primeiro alerta à detecção), TTA, TTM e TTR, em markdown."""
    raise NotImplementedError


def timeline_text(events: Sequence[IncidentEvent]) -> str:
    """Uma linha por evento relevante, com horário, origem (alerta, agente, chat ou pessoa) e o
    que aconteceu; sem os eventos internos de entrega de cards.
    """
    raise NotImplementedError


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
    raise NotImplementedError


def edited(
    pm: PostMortem, key: SectionKey, body: str, author: str, now: datetime
) -> tuple[PostMortem, SectionVersion]:
    """Seção editada por uma pessoa; rascunho passa a em revisão. Levanta InvalidPostMortemError
    em post mortem aprovado ou encerrado.
    """
    raise NotImplementedError


def regenerated(
    pm: PostMortem, key: SectionKey, body: str, now: datetime
) -> tuple[PostMortem, SectionVersion]:
    """Seção reescrita pelo agente; o status não muda."""
    raise NotImplementedError


def moved(pm: PostMortem, to: PostMortemStatus, author: str, now: datetime) -> PostMortem:
    """Muda o status seguindo o ciclo de vida; aprovar exige dono e prazo em todo item, encerrar
    exige todo item concluído ou aceito, e aprovado pode voltar a em revisão. Levanta
    InvalidPostMortemError.
    """
    raise NotImplementedError


def overdue(pm: PostMortem, today: date) -> tuple[ActionItem, ...]:
    """Itens abertos com prazo antes de `today`."""
    return tuple(
        a
        for a in pm.actions
        if a.status is ActionStatus.OPEN and a.due is not None and a.due < today
    )
