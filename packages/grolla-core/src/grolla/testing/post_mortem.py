"""Fakes do post mortem: `PostMortemStore` em memória e um `DocumentRenderer` que não gera PDF
de verdade, e o construtor `make_post_mortem` para os testes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from grolla.application.investigation import LlmUsage
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob, PostMortemSummary
from grolla.domain.incidents import Incident
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    SECTION_TITLES,
    ActionItem,
    ActionKind,
    PostMortem,
    PostMortemStatus,
    Section,
    SectionKey,
    SectionVersion,
    overdue,
)
from grolla.testing.builders import T0
from grolla.testing.incidents import InMemoryIncidentRepository

PENDING = "pendente"
RUNNING = "em_andamento"
DONE = "feito"
FAILED = "falho"


def make_post_mortem(
    incident_id: UUID | None = None,
    *,
    status: PostMortemStatus = PostMortemStatus.DRAFT,
    actions: Sequence[ActionItem] = (),
    at: datetime = T0,
) -> PostMortem:
    """Post mortem com todas as seções escritas pelo agente, na ordem do template."""
    sections = tuple(
        Section(key, f"{SECTION_TITLES[key]}: texto do agente.", AGENT_AUTHOR, at)
        for key in SectionKey
    )
    return PostMortem(
        incident_id=incident_id or uuid4(),
        status=status,
        sections=sections,
        actions=tuple(actions),
        created_at=at,
        updated_at=at,
        status_at=at,
    )


def make_action(
    description: str = "Criar alarme de conexões no banco",
    *,
    kind: ActionKind = ActionKind.DETECT,
    owner: str | None = None,
    due: date | None = None,
) -> ActionItem:
    return ActionItem(uuid4(), description, kind, owner, due)


@dataclass
class _Job:
    id: int
    incident_id: UUID
    key: SectionKey | None
    instruction: str | None
    author: str | None
    requested_at: datetime
    status: str = PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None

    def job(self, started_at: datetime) -> PostMortemJob:
        return PostMortemJob(
            self.id, self.incident_id, self.key, self.instruction, self.author, started_at
        )


class InMemoryPostMortemStore:
    """Post mortems em memória sobre um `InMemoryIncidentRepository`: o pedido do rascunho
    (`post_mortem_requests`, gravado na transação do encerramento) vira um pedido pendente, um só
    por incidente; o uso do modelo vai para `repository.usages`. O estado é público para os
    testes inspecionarem.
    """

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository
        self.documents: dict[UUID, PostMortem] = {}
        self.history: list[SectionVersion] = []  # em ordem de gravação
        self.jobs: list[_Job] = []

    def _sync(self) -> None:
        drafts = {j.incident_id for j in self.jobs if j.key is None}
        for incident_id, at in self.repository.post_mortem_requests.items():
            if incident_id not in drafts:
                self.jobs.append(_Job(len(self.jobs) + 1, incident_id, None, None, None, at))

    async def get(self, incident_id: UUID) -> PostMortem | None:
        return self.documents.get(incident_id)

    async def save(self, pm: PostMortem, versions: Sequence[SectionVersion]) -> None:
        self.documents[pm.incident_id] = pm
        self.history.extend(versions)

    async def versions(self, incident_id: UUID, key: SectionKey) -> list[SectionVersion]:
        mine = [v for v in reversed(self.history) if v.incident_id == incident_id and v.key == key]
        return sorted(mine, key=lambda v: v.at, reverse=True)

    async def summaries(self, filters: PostMortemFilters, today: date) -> list[PostMortemSummary]:
        found: list[PostMortemSummary] = []
        for pm in self.documents.values():
            incident = self.repository.incidents[pm.incident_id]
            detected = incident.detected_at.astimezone(UTC).date()
            if (
                (filters.status is not None and pm.status is not filters.status)
                or (filters.severity is not None and incident.severity is not filters.severity)
                or (filters.service is not None and filters.service not in incident.services)
                or (filters.since is not None and detected < filters.since)
                or (filters.until is not None and detected > filters.until)
            ):
                continue
            found.append(
                PostMortemSummary(incident, pm.status, pm.updated_at, len(overdue(pm, today)))
            )
        found.sort(key=lambda s: s.incident.detected_at, reverse=True)
        return found[: filters.limit]

    async def request_regeneration(
        self,
        incident_id: UUID,
        key: SectionKey,
        *,
        instruction: str | None,
        author: str,
        at: datetime,
    ) -> None:
        self.jobs.append(_Job(len(self.jobs) + 1, incident_id, key, instruction, author, at))

    async def claim(self, now: datetime) -> PostMortemJob | None:
        self._sync()
        pending = [j for j in self.jobs if j.status == PENDING]
        if not pending:
            return None
        job = min(pending, key=lambda j: (j.requested_at, j.id))
        job.status, job.started_at = RUNNING, now
        return job.job(now)

    async def stuck(self, started_before: datetime) -> list[PostMortemJob]:
        return [
            j.job(j.started_at)
            for j in self.jobs
            if j.status == RUNNING and j.started_at is not None and j.started_at < started_before
        ]

    async def finish(  # noqa: PLR0913 — mesmo port
        self,
        job: PostMortemJob,
        pm: PostMortem | None,
        *,
        versions: Sequence[SectionVersion],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        row = next((j for j in self.jobs if j.id == job.id), None)
        if row is None or row.status != RUNNING:
            return
        if pm is not None:
            await self.save(pm, versions)
        self.repository.usages.extend((job.incident_id, u, at) for u in usages)
        row.status = DONE if reason is None else FAILED
        row.finished_at, row.reason = at, reason


PDF_HEADER = b"%PDF-1.4\n"


class FakeDocumentRenderer:
    """Bytes que começam como um PDF e trazem o título, o status e as seções em UTF-8. Com
    `error`, levanta a exceção. Cada documento pedido fica em `rendered`.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.rendered: list[tuple[PostMortem, Incident]] = []

    def render(self, pm: PostMortem, incident: Incident) -> bytes:
        self.rendered.append((pm, incident))
        if self.error is not None:
            raise self.error
        lines = [incident.title, pm.status.value]
        lines += [f"{SECTION_TITLES[s.key]}\n{s.body}" for s in pm.sections]
        lines += [f"- {a.description} ({a.kind.value})" for a in pm.actions]
        return PDF_HEADER + "\n".join(lines).encode()
