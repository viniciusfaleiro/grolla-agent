"""Apresentação do post mortem: rótulos, transições oferecidas e leitura dos forms."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from grolla.application.post_mortem import PostMortemFilters
from grolla.domain.incidents import Severity
from grolla.domain.post_mortem import (
    DETERMINISTIC,
    NOT_GENERATED,
    ActionItem,
    ActionKind,
    ActionStatus,
    InvalidPostMortemError,
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
