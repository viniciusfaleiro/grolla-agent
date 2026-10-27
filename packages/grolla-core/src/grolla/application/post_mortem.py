"""Casos de uso do post mortem: gerar o rascunho, regenerar seções, editar, mover o status,
cuidar do plano de ação, listar e gerar o PDF.

Contrato do post mortem: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import LanguageModel, LlmUsage
from grolla.domain.incidents import Incident, Severity
from grolla.domain.post_mortem import (
    ActionItem,
    ActionKind,
    ActionStatus,
    PostMortem,
    PostMortemStatus,
    SectionKey,
    SectionVersion,
)


class PostMortemNotFoundError(LookupError):
    """O incidente não tem post mortem."""


class RenderError(Exception):
    """Não foi possível gerar o documento."""


@dataclass(frozen=True)
class PostMortemJob:
    id: int
    incident_id: UUID
    key: SectionKey | None  # None: rascunho inteiro
    instruction: str | None
    author: str | None  # quem pediu a regeneração
    started_at: datetime


@dataclass(frozen=True)
class PostMortemFilters:
    status: PostMortemStatus | None = None
    severity: Severity | None = None
    service: str | None = None
    since: date | None = None  # incidente detectado a partir de
    until: date | None = None  # incidente detectado até (inclusive)
    limit: int = 100


@dataclass(frozen=True)
class PostMortemSummary:
    incident: Incident
    status: PostMortemStatus
    updated_at: datetime
    overdue: int  # itens do plano vencidos (hoje, no relógio de quem lista)


class PostMortemStore(Protocol):
    async def get(self, incident_id: UUID) -> PostMortem | None: ...

    async def save(self, pm: PostMortem, versions: Sequence[SectionVersion]) -> None:
        """Grava o documento e as versões numa transação."""
        ...

    async def versions(self, incident_id: UUID, key: SectionKey) -> list[SectionVersion]:
        """Mais recentes primeiro."""
        ...

    async def summaries(self, filters: PostMortemFilters, today: date) -> list[PostMortemSummary]:
        """Mais recentes primeiro (pela detecção do incidente)."""
        ...

    async def request_regeneration(
        self,
        incident_id: UUID,
        key: SectionKey,
        *,
        instruction: str | None,
        author: str,
        at: datetime,
    ) -> None: ...

    async def claim(self, now: datetime) -> PostMortemJob | None:
        """O pedido pendente mais antigo (rascunho ou regeneração), marcado em andamento."""
        ...

    async def stuck(self, started_before: datetime) -> list[PostMortemJob]: ...

    async def finish(  # noqa: PLR0913 — o fim do pedido grava tudo de uma vez
        self,
        job: PostMortemJob,
        pm: PostMortem | None,
        *,
        versions: Sequence[SectionVersion],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        """Numa transação, só se o pedido está em andamento: grava o documento (se houver), as
        versões e o uso (etapa post_mortem), e encerra o pedido (falho com `reason`).
        """
        ...


class DocumentRenderer(Protocol):
    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        """O PDF do post mortem; levanta RenderError."""
        ...


@dataclass(frozen=True)
class PostMortemPolicy:
    max_tokens: int = 80_000
    timeout: timedelta = timedelta(minutes=3)


async def run_next_post_mortem(
    *,
    store: PostMortemStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: PostMortemPolicy,
) -> bool:
    """Gera o próximo rascunho ou regenera a próxima seção pedida; False se não havia pedido.
    Falha do modelo no rascunho grava as seções determinísticas e marca as outras.
    """
    raise NotImplementedError


async def reap_stuck_post_mortems(
    store: PostMortemStore, clock: Clock, policy: PostMortemPolicy
) -> int:
    raise NotImplementedError


async def edit_section(  # noqa: PLR0913 — a edição precisa de qual, o quê, quem e quando
    *,
    store: PostMortemStore,
    incident_id: UUID,
    key: SectionKey,
    body: str,
    author: str,
    clock: Clock,
) -> PostMortem:
    """Levanta PostMortemNotFoundError ou InvalidPostMortemError."""
    raise NotImplementedError


async def request_regeneration(  # noqa: PLR0913 — o pedido precisa de qual, como, quem e quando
    *,
    store: PostMortemStore,
    incident_id: UUID,
    key: SectionKey,
    instruction: str | None,
    author: str,
    clock: Clock,
) -> None:
    """Levanta PostMortemNotFoundError ou InvalidPostMortemError (aprovado/encerrado)."""
    raise NotImplementedError


async def move_status(
    *, store: PostMortemStore, incident_id: UUID, to: PostMortemStatus, author: str, clock: Clock
) -> PostMortem:
    raise NotImplementedError


async def add_action(  # noqa: PLR0913 — o item tem descrição, tipo, dono e prazo
    *,
    store: PostMortemStore,
    incident_id: UUID,
    description: str,
    kind: ActionKind,
    owner: str | None,
    due: date | None,
    author: str,
    clock: Clock,
) -> PostMortem:
    raise NotImplementedError


async def update_action(  # noqa: PLR0913 — o item pode mudar em qualquer campo
    *,
    store: PostMortemStore,
    incident_id: UUID,
    action_id: UUID,
    description: str,
    kind: ActionKind,
    owner: str | None,
    due: date | None,
    status: ActionStatus,
    author: str,
    clock: Clock,
) -> PostMortem:
    raise NotImplementedError


def overdue_items(pm: PostMortem, today: date) -> tuple[ActionItem, ...]:
    raise NotImplementedError


async def render_pdf(
    *,
    store: PostMortemStore,
    repository: IncidentRepository,
    renderer: DocumentRenderer,
    incident_id: UUID,
) -> bytes:
    """PDF de um post mortem aprovado ou encerrado; levanta PostMortemNotFoundError,
    InvalidPostMortemError (rascunho/em revisão) ou RenderError.
    """
    raise NotImplementedError
