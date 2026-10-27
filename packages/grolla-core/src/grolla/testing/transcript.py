"""Fake em memória do `TranscriptStore`, sobre o estado de um `InMemoryIncidentRepository`."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from grolla.application.investigation import LlmUsage
from grolla.application.transcript import TranscriptJob
from grolla.domain.incidents import IncidentEvent
from grolla.domain.transcript import Transcript
from grolla.testing.incidents import InMemoryIncidentRepository

PENDING = "pendente"
RUNNING = "em_andamento"
DONE = "concluida"
FAILED = "falhou"


@dataclass
class _Row:
    transcript: Transcript
    status: str = PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None
    retried_at: datetime | None = None  # quando voltou a pendente pela última vez


class InMemoryTranscriptStore:
    """Os eventos vão para o log do `repository` e o uso para `repository.usages`, como no
    banco. As linhas (`rows`, por id) ficam públicas para os testes inspecionarem.
    """

    def __init__(self, repository: InMemoryIncidentRepository) -> None:
        self.repository = repository
        self.rows: dict[int, _Row] = {}

    async def add(self, transcript: Transcript, event: IncidentEvent) -> Transcript:
        saved = replace(transcript, id=len(self.rows) + 1)
        self.rows[saved.id] = _Row(saved)
        async with self.repository.transaction() as uow:
            await uow.append([replace(event, data={**event.data, "transcript": saved.id})])
        return saved

    async def get(self, transcript_id: int) -> Transcript | None:
        row = self.rows.get(transcript_id)
        return None if row is None else row.transcript

    async def claim(self, now: datetime) -> TranscriptJob | None:
        pending = [r for r in self.rows.values() if r.status == PENDING]
        if not pending:
            return None
        row = min(pending, key=lambda r: (r.transcript.received_at, r.transcript.id))
        row.status, row.started_at = RUNNING, now
        return TranscriptJob(row.transcript, now)

    async def stuck(self, started_before: datetime) -> list[TranscriptJob]:
        return [
            TranscriptJob(r.transcript, r.started_at)
            for r in self.rows.values()
            if r.status == RUNNING and r.started_at is not None and r.started_at < started_before
        ]

    async def finish(
        self,
        job: TranscriptJob,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        row = self.rows.get(job.transcript.id)
        if row is None or row.status != RUNNING:
            return  # já terminada (pelo reaper ou por outro worker): o desfecho grava uma vez
        async with self.repository.transaction() as uow:
            await uow.append(events)
        self.repository.usages.extend((job.transcript.incident_id, u, at) for u in usages)
        row.status = DONE if reason is None else FAILED
        row.finished_at, row.reason = at, reason

    async def retry(self, transcript_id: int, at: datetime) -> bool:
        row = self.rows.get(transcript_id)
        if row is None or row.status != FAILED:
            return False
        row.status, row.started_at, row.finished_at, row.reason = PENDING, None, None, None
        row.retried_at = at
        return True
