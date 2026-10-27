"""Suíte de contrato de TranscriptStore.

O adapter sobrescreve `repository` e `store`, que precisam enxergar o mesmo armazenamento: os
eventos gravados pelo store aparecem no log do incidente. No banco real compartilhado, outros
testes deixam transcrições pendentes: cada teste cria incidentes novos (uuid4), esvazia a fila
com `drain` e só confere as próprias transcrições. O uso do modelo gravado pelo `finish` não tem
leitura no port: o adapter testa à parte.
"""

from datetime import datetime, timedelta

import pytest
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_TRANSCRIPT
from grolla.application.transcript import TranscriptJob, TranscriptStore
from grolla.domain.incidents import EventType, IncidentEvent
from grolla.domain.transcript import TRANSCRIPT_ORIGIN, Transcript
from grolla.testing.builders import T0, make_incident
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(seconds=30)
TEXT = "Pedro: vamos fazer o rollback agora, 03:12"


async def uploaded(
    repository: IncidentRepository, store: TranscriptStore, *, at: datetime = T0
) -> Transcript:
    """Abre um incidente novo e envia uma transcrição dele em `at`."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
    transcript = Transcript(0, incident.id, "Ana", TEXT, at)
    event = IncidentEvent(
        incident.id, at, EventType.TRANSCRIPT_RECEIVED, {"chars": len(TEXT), "truncated": False}
    )
    return await store.add(transcript, event)


async def drain(store: TranscriptStore, now: datetime = STARTED) -> list[TranscriptJob]:
    jobs: list[TranscriptJob] = []
    while (job := await store.claim(now)) is not None:
        jobs.append(job)
    return jobs


def mine(jobs: list[TranscriptJob], *transcripts: Transcript) -> list[TranscriptJob]:
    ids = {t.id for t in transcripts}
    return [j for j in jobs if j.transcript.id in ids]


def item(transcript: Transcript) -> IncidentEvent:
    data = {
        "kind": "decisao",
        "text": "fazer o rollback",
        "owner": None,
        "said_at": "03:12",
        "excerpt": TEXT,
        "origin": TRANSCRIPT_ORIGIN,
    }
    return IncidentEvent(
        transcript.incident_id, FINISHED, EventType.TRANSCRIPT_ITEM, data, TRANSCRIPT_ORIGIN
    )


class TranscriptStoreContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def store(self, repository: IncidentRepository) -> TranscriptStore:
        raise NotImplementedError

    async def test_add_saves_a_pending_transcript_and_the_event_with_its_id(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)

        assert saved.id > 0
        assert saved.author == "Ana"
        assert saved.text == TEXT
        assert await store.get(saved.id) == saved
        assert await repository.events_of(saved.incident_id) == [
            IncidentEvent(
                saved.incident_id,
                T0,
                EventType.TRANSCRIPT_RECEIVED,
                {"chars": len(TEXT), "truncated": False, "transcript": saved.id},
            )
        ]
        assert mine(await drain(store), saved) == [TranscriptJob(saved, STARTED)]

    async def test_get_of_an_unknown_transcript_is_none(self, store: TranscriptStore) -> None:
        assert await store.get(2**31 - 1) is None

    async def test_claim_takes_the_oldest_pending_first(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        later = await uploaded(repository, store, at=T0 + timedelta(seconds=2))
        earlier = await uploaded(repository, store, at=T0 + timedelta(seconds=1))

        jobs = mine(await drain(store), earlier, later)

        assert [j.transcript for j in jobs] == [earlier, later]

    async def test_stuck_lists_running_transcripts_started_before(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)
        (job,) = mine(await drain(store), saved)

        assert job in await store.stuck(STARTED + timedelta(seconds=1))
        assert job not in await store.stuck(STARTED)

    async def test_finish_saves_the_events_once_and_ends_the_transcript(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)
        (job,) = mine(await drain(store), saved)
        received = await repository.events_of(saved.incident_id)

        await store.finish(job, [item(saved)], [fake_usage(800, STAGE_TRANSCRIPT)], None, FINISHED)
        await store.finish(job, [item(saved)], [], "tempo esgotado", FINISHED)  # ignorado

        assert await repository.events_of(saved.incident_id) == [*received, item(saved)]
        assert job not in await store.stuck(FINISHED + timedelta(hours=1))
        assert mine(await drain(store, FINISHED), saved) == []
        assert await store.retry(saved.id, FINISHED) is False  # concluída não reprocessa

    async def test_finish_does_nothing_for_a_pending_transcript(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)
        received = await repository.events_of(saved.incident_id)

        await store.finish(TranscriptJob(saved, STARTED), [item(saved)], [], None, FINISHED)

        assert await repository.events_of(saved.incident_id) == received
        assert mine(await drain(store), saved) == [TranscriptJob(saved, STARTED)]

    async def test_failed_transcript_goes_back_to_pending_on_retry(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)
        (job,) = mine(await drain(store), saved)
        failed = IncidentEvent(
            saved.incident_id,
            FINISHED,
            EventType.TRANSCRIPT_FAILED,
            {"transcript": saved.id, "reason": "LLM indisponível"},
        )

        await store.finish(job, [failed], [], "LLM indisponível", FINISHED)

        assert failed in await repository.events_of(saved.incident_id)
        assert mine(await drain(store, FINISHED), saved) == []
        assert await store.retry(saved.id, FINISHED) is True
        assert await store.retry(saved.id, FINISHED) is False  # já pendente
        later = FINISHED + timedelta(minutes=1)
        assert mine(await drain(store, later), saved) == [TranscriptJob(saved, later)]

    async def test_retry_of_a_pending_or_unknown_transcript_is_false(
        self, repository: IncidentRepository, store: TranscriptStore
    ) -> None:
        saved = await uploaded(repository, store)

        assert await store.retry(saved.id, FINISHED) is False
        assert await store.retry(2**31 - 1, FINISHED) is False
