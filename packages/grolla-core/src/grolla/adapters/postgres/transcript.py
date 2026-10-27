"""Transcrições de call no PostgreSQL (migração 0013), com a fila de extração.

Contrato da transcrição de call: os corpos são do grolla-persistencia.
"""

from collections.abc import Sequence
from datetime import datetime

from grolla.adapters.postgres.database import PostgresDatabase
from grolla.application.investigation import LlmUsage
from grolla.application.transcript import TranscriptJob
from grolla.domain.incidents import IncidentEvent
from grolla.domain.transcript import Transcript


class PostgresTranscriptStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def add(self, transcript: Transcript, event: IncidentEvent) -> Transcript:
        raise NotImplementedError

    async def get(self, transcript_id: int) -> Transcript | None:
        raise NotImplementedError

    async def claim(self, now: datetime) -> TranscriptJob | None:
        raise NotImplementedError

    async def stuck(self, started_before: datetime) -> list[TranscriptJob]:
        raise NotImplementedError

    async def finish(
        self,
        job: TranscriptJob,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        reason: str | None,
        at: datetime,
    ) -> None:
        raise NotImplementedError

    async def retry(self, transcript_id: int, at: datetime) -> bool:
        raise NotImplementedError
