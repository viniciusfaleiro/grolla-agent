"""Casos de uso do post mortem: gerar o rascunho, regenerar seções, editar, mover o status,
cuidar do plano de ação, listar e gerar o PDF.

O rascunho e a regeneração rodam no worker, a partir da fila do `PostMortemStore`; o resto roda
na página. As regras (ciclo de vida, plano de ação, métricas e timeline) são do domínio.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from grolla.application.analysis import REAP_GRACE, failure_reason
from grolla.application.chat import chat_context
from grolla.application.clock import Clock
from grolla.application.follow_up import ImpactPolicy, impact_of
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    LanguageModel,
    LlmUsage,
    PostMortemBrief,
    TokenBudget,
)
from grolla.domain.analysis import PARTIAL_REASON_TEXT, PartialReason
from grolla.domain.incidents import Alert, Incident, IncidentEvent, Severity
from grolla.domain.post_mortem import (
    AUTHOR_REQUIRED,
    DETERMINISTIC,
    REGENERATE_REASON,
    UNKNOWN_ACTION,
    ActionItem,
    ActionKind,
    ActionStatus,
    InvalidPostMortemError,
    PostMortem,
    PostMortemDraft,
    PostMortemStatus,
    SectionKey,
    SectionVersion,
    action_changed,
    check_regenerable,
    drafted,
    edited,
    metrics_text,
    moved,
    overdue,
    regenerated,
    timeline_text,
)

NO_INCIDENT = "incidente não existe"
NO_POST_MORTEM = "o incidente não tem post mortem"
RECALCULATED_REASON = "recalculada do log"
PDF_NOT_READY = "o PDF só existe para post mortem aprovado ou encerrado"
INTERRUPTED = PARTIAL_REASON_TEXT[PartialReason.INTERRUPTED]
PDF_STATUSES = frozenset({PostMortemStatus.APPROVED, PostMortemStatus.CLOSED})


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


async def post_mortem_brief(
    repository: IncidentRepository,
    incident: Incident,
    *,
    current: PostMortem | None,
    impact: ImpactPolicy,
    now: datetime,
) -> PostMortemBrief:
    """O contexto do incidente como no chat (análise, validação, timeline e chat), com os
    ciclos de acompanhamento, as métricas e a timeline completa calculadas do log.
    """
    events = await repository.events_of(incident.id)
    cycles = await repository.cycles_of(incident.id)
    lines = [
        chat_context(
            incident,
            await repository.analysis_of(incident.id),
            await repository.verification_of(incident.id),
            events,
            await repository.chat_of(incident.id),
        ),
        "",
        "Ciclos de acompanhamento:",
    ]
    lines += [
        f"- {c.number} ({c.trend.value if c.trend else c.status.value}): {c.summary}"
        for c in cycles
    ]
    if not cycles:
        lines.append("- nenhum ciclo")
    lines += [
        "",
        "Métricas (calculadas do log):",
        metrics_text(incident, await repository.alerts_of(incident.id)),
        "",
        "Timeline completa (calculada do log, com a origem de cada item):",
        timeline_text(events),
    ]
    text = "\n".join(lines)
    return PostMortemBrief(
        incident, text, current, await impact_of(repository, incident, impact), now
    )


async def run_next_post_mortem(  # noqa: PLR0913 — o caso de uso liga os ports do post mortem
    *,
    store: PostMortemStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: PostMortemPolicy,
    impact: ImpactPolicy | None = None,
) -> bool:
    """Gera o próximo rascunho ou regenera a próxima seção pedida; False se não havia pedido.
    Falha do modelo no rascunho grava as seções determinísticas e marca as outras.

    Seção determinística (métricas, timeline) é recalculada do log, sem o modelo. Post mortem
    aprovado ou encerrado não é regenerado: o pedido termina falho com o motivo.
    """
    job = await store.claim(clock.now())
    if job is None:
        return False
    run = _Run(store, repository, model, clock, policy, impact or ImpactPolicy(), job)
    incident = await repository.get_incident(job.incident_id)
    if incident is None:  # o pedido referencia o incidente: só acontece com dado corrompido
        await run.end(None, (), NO_INCIDENT)
    elif job.key is None:
        await run.draft(incident)
    else:
        await run.regenerate(incident, job.key)
    return True


@dataclass(frozen=True)
class _Run:
    """Um pedido da fila em execução."""

    store: PostMortemStore
    repository: IncidentRepository
    model: LanguageModel
    clock: Clock
    policy: PostMortemPolicy
    impact: ImpactPolicy
    job: PostMortemJob

    async def end(
        self,
        pm: PostMortem | None,
        usages: Sequence[LlmUsage],
        reason: str | None,
        versions: Sequence[SectionVersion] = (),
    ) -> None:
        await self.store.finish(
            self.job, pm, versions=versions, usages=usages, reason=reason, at=self.clock.now()
        )

    async def draft(self, incident: Incident) -> None:
        if await self.store.get(incident.id) is not None:  # já gerado: nada a fazer
            await self.end(None, (), None)
            return
        brief = await post_mortem_brief(
            self.repository, incident, current=None, impact=self.impact, now=self.clock.now()
        )
        budget = TokenBudget(self.policy.max_tokens)
        deadline = asyncio.timeout(self.policy.timeout.total_seconds())
        try:
            async with deadline:
                draft = await self.model.draft_post_mortem(brief, budget)
        except Exception as error:  # qualquer falha vira "não gerada: <motivo>" nas seções
            failure = PARTIAL_REASON_TEXT[failure_reason(error, timed_out=deadline.expired())]
            draft = None
        else:
            failure = None
        pm = await _drafted(self.repository, incident, draft, failure, self.clock.now())
        await self.end(pm, budget.usages, failure)

    async def regenerate(self, incident: Incident, key: SectionKey) -> None:
        current = await self.store.get(incident.id)
        if current is None:
            await self.end(None, (), NO_POST_MORTEM)
            return
        budget = TokenBudget(self.policy.max_tokens)
        if key in DETERMINISTIC:
            body = await _deterministic(self.repository, incident, key)
            reason = RECALCULATED_REASON
        else:
            deadline = asyncio.timeout(self.policy.timeout.total_seconds())
            try:
                check_regenerable(current)
                brief = await post_mortem_brief(
                    self.repository,
                    incident,
                    current=current,
                    impact=self.impact,
                    now=self.clock.now(),
                )
                async with deadline:
                    body = await self.model.regenerate_section(
                        brief, key, self.job.instruction, budget
                    )
            except InvalidPostMortemError as error:
                await self.end(None, (), str(error))
                return
            except Exception as error:  # a seção fica como estava; o pedido termina falho
                failed = failure_reason(error, timed_out=deadline.expired())
                await self.end(None, budget.usages, PARTIAL_REASON_TEXT[failed])
                return
            reason = REGENERATE_REASON
        latest = await self.store.get(incident.id) or current  # edições feitas enquanto isso
        try:
            pm, version = regenerated(latest, key, body, self.clock.now())
        except InvalidPostMortemError as error:
            await self.end(None, budget.usages, str(error))
            return
        version = replace(version, reason=_asked(reason, self.job))
        await self.end(pm, budget.usages, None, [version])


def _asked(reason: str, job: PostMortemJob) -> str:
    """ "regenerada pelo agente a pedido de Ana: incluir o alarme"."""
    asked = f"{reason} a pedido de {job.author}" if job.author else reason
    return f"{asked}: {job.instruction}" if job.instruction else asked


async def _deterministic(
    repository: IncidentRepository, incident: Incident, key: SectionKey
) -> str:
    if key is SectionKey.METRICS:
        return metrics_text(incident, await repository.alerts_of(incident.id))
    return timeline_text(await repository.events_of(incident.id))


async def _drafted(
    repository: IncidentRepository,
    incident: Incident,
    draft: PostMortemDraft | None,
    failure: str | None,
    now: datetime,
) -> PostMortem:
    alerts: Sequence[Alert] = await repository.alerts_of(incident.id)
    events: Sequence[IncidentEvent] = await repository.events_of(incident.id)
    return drafted(incident, alerts, events, draft=draft, failure=failure, now=now)


async def reap_stuck_post_mortems(
    store: PostMortemStore, repository: IncidentRepository, clock: Clock, policy: PostMortemPolicy
) -> int:
    """Encerra como falhos (interrompidos) os pedidos presos; devolve quantos. O rascunho
    interrompido ainda grava o post mortem, com as seções do modelo "não gerada: interrompida".
    """
    now = clock.now()
    jobs = await store.stuck(now - policy.timeout - REAP_GRACE)
    for job in jobs:
        pm = None
        incident = await repository.get_incident(job.incident_id)
        if job.key is None and incident is not None and await store.get(job.incident_id) is None:
            pm = await _drafted(repository, incident, None, INTERRUPTED, now)
        await store.finish(job, pm, versions=(), usages=(), reason=INTERRUPTED, at=now)
    return len(jobs)


async def _document(store: PostMortemStore, incident_id: UUID) -> PostMortem:
    pm = await store.get(incident_id)
    if pm is None:
        raise PostMortemNotFoundError(NO_POST_MORTEM)
    return pm


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
    pm, version = edited(await _document(store, incident_id), key, body, author, clock.now())
    await store.save(pm, [version])
    return pm


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
    author = author.strip()
    if not author:
        raise InvalidPostMortemError(AUTHOR_REQUIRED)
    check_regenerable(await _document(store, incident_id))
    await store.request_regeneration(
        incident_id,
        key,
        instruction=(instruction or "").strip() or None,
        author=author,
        at=clock.now(),
    )


async def move_status(
    *, store: PostMortemStore, incident_id: UUID, to: PostMortemStatus, author: str, clock: Clock
) -> PostMortem:
    """Levanta PostMortemNotFoundError ou InvalidPostMortemError (com o item que falta)."""
    pm = moved(await _document(store, incident_id), to, author, clock.now())
    await store.save(pm, [])
    return pm


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
    """Levanta PostMortemNotFoundError ou InvalidPostMortemError."""
    item = ActionItem(uuid4(), description, kind, owner, due)
    pm = action_changed(await _document(store, incident_id), item, author, clock.now())
    await store.save(pm, [])
    return pm


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
    """Levanta PostMortemNotFoundError ou InvalidPostMortemError (item que não existe)."""
    pm = await _document(store, incident_id)
    if all(a.id != action_id for a in pm.actions):
        raise InvalidPostMortemError(UNKNOWN_ACTION)
    item = ActionItem(action_id, description, kind, owner, due, status)
    updated = action_changed(pm, item, author, clock.now())
    await store.save(updated, [])
    return updated


def overdue_items(pm: PostMortem, today: date) -> tuple[ActionItem, ...]:
    return overdue(pm, today)


async def list_post_mortems(
    store: PostMortemStore, filters: PostMortemFilters, clock: Clock
) -> list[PostMortemSummary]:
    """Mais recentes primeiro, com os itens vencidos contados no dia de hoje."""
    return await store.summaries(filters, clock.now().date())


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
    pm = await _document(store, incident_id)
    if pm.status not in PDF_STATUSES:
        raise InvalidPostMortemError(PDF_NOT_READY)
    incident = await repository.get_incident(incident_id)
    if incident is None:  # o post mortem referencia o incidente: só com dado corrompido
        raise PostMortemNotFoundError(NO_INCIDENT)
    return renderer.render(pm, incident)
