"""Apresentação do post mortem: rótulos, transições oferecidas e leitura dos forms."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from grolla.application.post_mortem import PostMortemFilters, PostMortemSummary
from grolla.domain.incidents import Severity
from grolla.domain.post_mortem import (
    DETERMINISTIC,
    NOT_GENERATED,
    ActionItem,
    ActionKind,
    ActionStatus,
    InvalidPostMortemError,
    PostMortem,
    PostMortemStatus,
    Section,
    SectionKey,
)

PM_STATUS_LABELS = {
    PostMortemStatus.DRAFT: "Rascunho",
    PostMortemStatus.REVIEW: "Em revisão",
    PostMortemStatus.APPROVED: "Aprovado",
    PostMortemStatus.CLOSED: "Encerrado",
}

ACTION_KIND_LABELS = {
    ActionKind.PREVENT: "prevenir",
    ActionKind.DETECT: "detectar antes",
    ActionKind.MITIGATE: "mitigar mais rápido",
}

ACTION_STATUS_LABELS = {
    ActionStatus.OPEN: "aberto",
    ActionStatus.DONE: "concluído",
    ActionStatus.ACCEPTED: "aceito",
}


@dataclass(frozen=True)
class Transition:
    to: PostMortemStatus
    label: str
    primary: bool = False


# Só os botões que fazem sentido em cada status; quem valida a mudança é o domínio.
TRANSITIONS: Mapping[PostMortemStatus, tuple[Transition, ...]] = {
    PostMortemStatus.DRAFT: (Transition(PostMortemStatus.REVIEW, "Começar revisão", True),),
    PostMortemStatus.REVIEW: (Transition(PostMortemStatus.APPROVED, "Aprovar", True),),
    PostMortemStatus.APPROVED: (
        Transition(PostMortemStatus.CLOSED, "Encerrar", True),
        Transition(PostMortemStatus.REVIEW, "Voltar para revisão"),
    ),
    PostMortemStatus.CLOSED: (),
}

# Aprovado e encerrado não mudam de texto: o PDF é desta versão.
FROZEN = frozenset({PostMortemStatus.APPROVED, PostMortemStatus.CLOSED})


def can_edit(status: PostMortemStatus) -> bool:
    return status not in FROZEN


def can_regenerate(status: PostMortemStatus, key: SectionKey) -> bool:
    """Métricas e timeline vêm do log; o modelo só reescreve as outras."""
    return status not in FROZEN and key not in DETERMINISTIC


def has_pdf(status: PostMortemStatus) -> bool:
    return status in FROZEN


def not_generated(section: Section) -> bool:
    return section.body.startswith(NOT_GENERATED)


def _choice[E: (PostMortemStatus, Severity, ActionKind, ActionStatus)](
    kind: type[E], value: str, what: str
) -> E:
    try:
        return kind(value)
    except ValueError as error:
        raise InvalidPostMortemError(f"{what} inválido") from error


def _date(value: str, what: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise InvalidPostMortemError(f"{what} inválido: use a data do calendário") from error


def section_key(value: str) -> SectionKey | None:
    try:
        return SectionKey(value)
    except ValueError:
        return None


def target_status(form: Mapping[str, str]) -> PostMortemStatus:
    return _choice(PostMortemStatus, form.get("para", ""), "status")


def _optional(value: str | None) -> str | None:
    return value or None


@dataclass(frozen=True)
class ActionFields:
    description: str
    kind: ActionKind
    owner: str | None
    due: date | None
    status: ActionStatus


def new_action(form: Mapping[str, str]) -> ActionFields:
    """Item novo: descrição, tipo, dono e prazo (os dois opcionais); sempre aberto."""
    return ActionFields(
        description=form.get("descricao", ""),
        kind=_choice(ActionKind, form.get("tipo", ""), "tipo"),
        owner=_optional(form.get("dono")),
        due=_date(form.get("prazo", ""), "prazo"),
        status=ActionStatus.OPEN,
    )


def changed_action(item: ActionItem, form: Mapping[str, str]) -> ActionFields:
    """O item com os campos que vieram no form; os ausentes ficam como estão, e vazio limpa o
    dono e o prazo. Assim "concluir" manda só `situacao`.
    """
    owner = _optional(form["dono"]) if "dono" in form else item.owner
    due = _date(form["prazo"], "prazo") if "prazo" in form else item.due
    kind = _choice(ActionKind, form["tipo"], "tipo") if "tipo" in form else item.kind
    status = (
        _choice(ActionStatus, form["situacao"], "situação") if "situacao" in form else item.status
    )
    return ActionFields(
        description=form.get("descricao", item.description),
        kind=kind,
        owner=owner,
        due=due,
        status=status,
    )


def _enum_or_none[E: (PostMortemStatus, Severity)](kind: type[E], value: str) -> E | None:
    try:
        return kind(value) if value else None
    except ValueError:
        return None


def _day_or_none(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def list_filters(params: Mapping[str, str]) -> PostMortemFilters:
    """Os filtros da query (`status`, `severidade`, `servico`, `de`, `ate`); valor inválido vale
    como sem filtro, para a lista não quebrar por um link velho.
    """
    return PostMortemFilters(
        status=_enum_or_none(PostMortemStatus, params.get("status", "")),
        severity=_enum_or_none(Severity, params.get("severidade", "")),
        service=params.get("servico", "").strip() or None,
        since=_day_or_none(params.get("de", "")),
        until=_day_or_none(params.get("ate", "")),
    )


# ---- Lista e documento redesenhados ----

_SENTENCE = re.compile(r"(?<=[.!?])\s")


def first_sentence(body: str) -> str:
    """A primeira frase do texto, sem marcador de lista: a causa numa linha na lista."""
    for line in body.splitlines():
        text = line.strip().lstrip("-*• ").strip()
        if text:
            return _SENTENCE.split(text, maxsplit=1)[0]
    return ""


def plan_status(actions: tuple[ActionItem, ...]) -> str:
    """'3 itens sem dono', '2 de 4 concluídos' ou 'sem itens'."""
    if not actions:
        return "sem itens"
    ownerless = sum(1 for a in actions if not a.owner or a.due is None)
    if ownerless:
        return f"{ownerless} {'item' if ownerless == 1 else 'itens'} sem dono ou prazo"
    done = sum(1 for a in actions if a.status is not ActionStatus.OPEN)
    return f"{done} de {len(actions)} concluídos"


@dataclass(frozen=True)
class PmRow:
    summary: PostMortemSummary
    cause: str
    plan: str
    evaluated: bool


def pm_row(summary: PostMortemSummary, pm: PostMortem | None) -> PmRow:
    if pm is None:
        return PmRow(summary, "", "", evaluated=False)
    resume = next((s.body for s in pm.sections if s.key is SectionKey.SUMMARY), "")
    return PmRow(
        summary,
        cause="" if resume.startswith(NOT_GENERATED) else first_sentence(resume),
        plan=plan_status(pm.actions),
        evaluated=pm.evaluation.author is not None,
    )


@dataclass(frozen=True)
class ListExtras:
    """Filtros da lista que olham o documento: só com vencidos, só sem avaliação."""

    overdue_only: bool = False
    unevaluated_only: bool = False

    def matches(self, row: PmRow) -> bool:
        return (not self.overdue_only or row.summary.overdue > 0) and (
            not self.unevaluated_only or not row.evaluated
        )


def list_extras(params: Mapping[str, str]) -> ListExtras:
    return ListExtras(
        overdue_only=params.get("vencidos", "") == "1",
        unevaluated_only=params.get("sem_avaliacao", "") == "1",
    )


@dataclass(frozen=True)
class Totals:
    drafts: int
    review: int
    overdue: int  # itens vencidos somados
    closed_this_month: int


def totals_of(summaries: list[PostMortemSummary], today: date) -> Totals:
    return Totals(
        drafts=sum(1 for s in summaries if s.status is PostMortemStatus.DRAFT),
        review=sum(1 for s in summaries if s.status is PostMortemStatus.REVIEW),
        overdue=sum(s.overdue for s in summaries),
        closed_this_month=sum(
            1
            for s in summaries
            if s.status is PostMortemStatus.CLOSED
            and (s.updated_at.year, s.updated_at.month) == (today.year, today.month)
        ),
    )


@dataclass(frozen=True)
class OutlineItem:
    key: SectionKey
    number: int
    who: str  # Grolla, do log ou o nome de quem editou por último
    missing: str  # o que falta na seção, ou ""


def outline_of(pm: PostMortem, overdue: int) -> tuple[OutlineItem, ...]:
    """O índice lateral: quem escreveu cada seção e o que falta para fechar o documento."""
    items: list[OutlineItem] = []
    for number, section in enumerate(pm.sections, start=1):
        who = "do log" if section.key in DETERMINISTIC else section.author
        missing = ""
        if section.key is SectionKey.ACTIONS:
            ownerless = sum(1 for a in pm.actions if not a.owner or a.due is None)
            if overdue:
                missing = f"{overdue} {'vencido' if overdue == 1 else 'vencidos'}"
            elif ownerless:
                missing = f"{ownerless} sem dono"
        if section.key is SectionKey.AGENT and pm.evaluation.author is None:
            missing = "falta avaliar"
        if not_generated(section):
            missing = "não gerada"
        items.append(OutlineItem(section.key, number, who, missing))
    return tuple(items)
