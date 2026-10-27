"""Fakes em memória dos ports da memória viva: `MemoryStore`, `TextEmbedder`, `LearningQueue`
e `StudyQueue`.

A busca textual do fake é simples: um registro (ou trecho) casa quando tem alguma palavra (de 3
letras ou mais, sem diferenciar maiúsculas) da busca. A busca híbrida dos trechos junta, por
RRF (k=60), o ranking do cosseno com o da sobreposição de palavras, como o adapter real.
"""

import math
import re
import zlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from uuid import UUID

from grolla.application.investigation import LlmUsage
from grolla.application.memory import (
    LEARNING_ATTEMPTS,
    LearningFailure,
    LearningJob,
    ServiceStudy,
    StudyMode,
    StudyRun,
    StudyStatus,
)
from grolla.application.memory_store import (
    Embedded,
    EmbeddingError,
    EmbeddingKind,
    Query,
    SubjectSummary,
)
from grolla.domain.memory import (
    DOCUMENT_LIMIT,
    ENVIRONMENT,
    EPISODE_LIMIT,
    Chunk,
    ChunkHit,
    ChunkOrigin,
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    MemoryVersion,
    SubjectKind,
)
from grolla.testing.incidents import InMemoryIncidentRepository

_WORD = re.compile(r"\w{3,}")
_KIND_ORDER = {SubjectKind.ENVIRONMENT: 0, SubjectKind.SERVICE: 1, SubjectKind.INTEGRATION: 2}
_RECORD_ORDER = {MemoryKind.DOSSIER: 0, MemoryKind.EPISODE: 1, MemoryKind.NOTE: 2}


def _words_in_order(text: str) -> list[str]:
    return [match.group() for match in _WORD.finditer(text.casefold())]


def _words(text: str) -> set[str]:
    return set(_words_in_order(text))


def _overlap(record: Memory, words: set[str]) -> int:
    return len(words & _words(f"{record.title} {record.body}"))


FAKE_EMBEDDING_MODEL = "fake-bag-of-words"
FAKE_EMBEDDING_DIMENSION = 512
RRF_K = 60
VECTOR_CANDIDATES = 50  # trechos mais próximos que entram na fusão


class FakeTextEmbedder:
    """Embeddings determinísticos e sem rede: saco de palavras normalizado.

    Cada palavra (3 letras ou mais, sem diferenciar maiúsculas) cai numa dimensão por hash. Com
    `concepts` (palavra → conceito), palavras diferentes caem na dimensão do mesmo conceito, o
    que roteiriza paráfrases que só o vetor acha. Texto sem palavra vira um vetor fixo. Com
    `failing`, toda chamada levanta EmbeddingError. Cada chamada fica em `calls`.
    """

    def __init__(
        self,
        concepts: Mapping[str, str] | None = None,
        *,
        model: str = FAKE_EMBEDDING_MODEL,
        failing: bool = False,
    ) -> None:
        self.concepts = {w.casefold(): c.casefold() for w, c in (concepts or {}).items()}
        self._model = model
        self.failing = failing
        self.calls: list[tuple[tuple[str, ...], EmbeddingKind]] = []

    @property
    def model(self) -> str:
        return self._model

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        self.calls.append((tuple(texts), kind))
        if self.failing:
            raise EmbeddingError("modelo de embeddings fora do ar no fake")
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * FAKE_EMBEDDING_DIMENSION
        for word in _words_in_order(text):
            token = self.concepts.get(word, word)
            vector[zlib.crc32(token.encode()) % FAKE_EMBEDDING_DIMENSION] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@dataclass
class _Indexed:
    """Os trechos de um registro, ou da documentação de um serviço, com um modelo."""

    model: str
    at: datetime
    chunks: list[Embedded] = field(default_factory=list[Embedded])


def _fused(entries: Sequence[Embedded], query: Query) -> list[ChunkHit]:
    """RRF (k=60) do ranking por cosseno (os VECTOR_CANDIDATES mais próximos) e do ranking por
    palavras em comum (só os que têm alguma); maior primeiro. Sem vetor, só o textual.
    """
    scores: dict[int, float] = {}
    words = _words(query.text)
    overlaps = [len(words & _words(f"{c.heading} {c.text}")) for c, _ in entries]
    textual = sorted((i for i, o in enumerate(overlaps) if o > 0), key=lambda i: -overlaps[i])
    rankings = [textual]
    if query.vector is not None:
        vector = query.vector
        nearest = sorted(range(len(entries)), key=lambda i: -_cosine(entries[i][1], vector))
        rankings.append(nearest[:VECTOR_CANDIDATES])
    for ranking in rankings:
        for rank, i in enumerate(ranking, start=1):
            scores[i] = scores.get(i, 0.0) + 1 / (RRF_K + rank)
    ranked = sorted(scores, key=lambda i: -scores[i])
    return [ChunkHit(entries[i][0], scores[i]) for i in ranked]


class InMemoryMemoryStore:
    """Memória em memória. O estado é público para os testes inspecionarem."""

    def __init__(self) -> None:
        self.records: dict[UUID, Memory] = {}
        self.history: list[MemoryVersion] = []
        self.chunks: dict[UUID, _Indexed] = {}  # trechos de cada registro
        self.documents: dict[str, _Indexed] = {}  # trechos da documentação de cada serviço

    def _active(self, kind: MemoryKind) -> list[Memory]:
        return [r for r in self.records.values() if r.active and r.kind is kind]

    async def context_for(
        self,
        services: Sequence[str],
        alert_names: Sequence[str],
        text: str,
        query: Query | None = None,
    ) -> MemoryContext:
        dossiers = {d.subject: d for d in self._active(MemoryKind.DOSSIER)}
        mine = [dossiers[s] for s in (ENVIRONMENT, *services) if s in dossiers]
        service_bodies = " ".join(dossiers[s].body.casefold() for s in services if s in dossiers)
        integrations = sorted(
            (
                d
                for d in dossiers.values()
                if d.subject_kind is SubjectKind.INTEGRATION
                and d.subject.casefold() in service_bodies
            ),
            key=lambda d: d.subject,
        )
        subjects = {ENVIRONMENT, *services, *(d.subject for d in integrations)}
        notes = sorted(
            (
                n
                for n in self._active(MemoryKind.NOTE)
                if n.subject in subjects and not n.consolidated
            ),
            key=lambda n: n.updated_at,
            reverse=True,
        )
        names = [name.casefold() for name in alert_names if name]
        words = _words(text)

        def same_rule(episode: Memory) -> bool:
            content = f"{episode.title} {episode.body}".casefold()
            return episode.subject in services and any(name in content for name in names)

        dossiers_found = tuple(mine + integrations)
        all_episodes = self._active(MemoryKind.EPISODE)
        if query is not None and query.vector is not None:
            first = sorted(
                (e for e in all_episodes if same_rule(e)), key=lambda e: e.updated_at, reverse=True
            )
            hits = self._hybrid(None, query, lambda c: c.kind is MemoryKind.EPISODE)
            similar = [self.records[h.chunk.memory_id] for h in hits if h.chunk.memory_id]
            episodes = list(dict.fromkeys([*first, *similar]))[:EPISODE_LIMIT]
            documents = [
                h
                for h in self._hybrid(None, query, lambda c: c.origin is ChunkOrigin.DOCUMENT)
                if h.chunk.subject in services
            ]
            return MemoryContext(
                dossiers_found, tuple(notes), tuple(episodes), tuple(documents[:DOCUMENT_LIMIT])
            )
        ranked = sorted(
            ((same_rule(e), _overlap(e, words), e.updated_at, e) for e in all_episodes),
            key=lambda item: item[:3],
            reverse=True,
        )
        episodes = [e for rule, score, _, e in ranked if rule or score > 0][:EPISODE_LIMIT]
        return MemoryContext(dossiers_found, tuple(notes), tuple(episodes))

    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]:
        words = _words(query)
        found = [
            r
            for r in self.records.values()
            if r.active
            and (subject is None or r.subject == subject)
            and (not words or _overlap(r, words) > 0)
        ]
        found.sort(key=lambda r: (r.confidence is Confidence.CONFIRMED, r.updated_at), reverse=True)
        return found[:limit]

    async def get(self, memory_id: UUID) -> Memory | None:
        return self.records.get(memory_id)

    async def dossier(self, subject: str) -> Memory | None:
        found = [
            r
            for r in self.records.values()
            if r.kind is MemoryKind.DOSSIER and r.subject == subject
        ]
        return max(found, key=lambda r: (r.active, r.updated_at), default=None)

    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None:
        return next(
            (
                r
                for r in self.records.values()
                if r.kind is MemoryKind.EPISODE
                and r.incident_id == incident_id
                and r.subject == subject
            ),
            None,
        )

    async def all_active(self) -> list[Memory]:
        return [r for r in self.records.values() if r.active]

    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        for record, version in records:
            self.records[record.id] = record
            if version is not None:
                self.history.append(version)

    async def subjects(self) -> list[SubjectSummary]:
        by_subject: dict[str, list[Memory]] = {}
        for record in self.records.values():
            by_subject.setdefault(record.subject, []).append(record)
        summaries = [
            SubjectSummary(
                subject=subject,
                kind=records[0].subject_kind,
                records=sum(r.active for r in records),
                updated_at=max(r.updated_at for r in records),
                studied_at=None,  # o fake não liga a memória às rodadas de estudo
            )
            for subject, records in by_subject.items()
        ]
        return sorted(summaries, key=lambda s: (_KIND_ORDER[s.kind], s.subject))

    async def of_subject(self, subject: str) -> list[Memory]:
        mine = [r for r in self.records.values() if r.subject == subject]
        mine.sort(key=lambda r: r.updated_at, reverse=True)
        return sorted(mine, key=lambda r: _RECORD_ORDER[r.kind])

    async def versions(self, memory_id: UUID) -> list[MemoryVersion]:
        mine = [v for v in self.history if v.memory_id == memory_id]
        return sorted(mine, key=lambda v: v.at, reverse=True)

    # ---- memória com RAG ----

    def _hybrid(
        self, subject: str | None, query: Query, keep: Callable[[Chunk], bool]
    ) -> list[ChunkHit]:
        """A busca híbrida nos trechos que `keep` aceita, de registros ativos e de documentação,
        do modelo da query (de qualquer modelo sem ele) e do assunto (todos com None).
        """
        indexed = [e for memory_id, e in self.chunks.items() if self.records[memory_id].active]
        indexed += self.documents.values()
        entries = [
            item
            for entry in indexed
            if query.model in {None, entry.model}
            for item in entry.chunks
            if (subject is None or item[0].subject == subject) and keep(item[0])
        ]
        return _fused(entries, query)

    async def search_chunks(self, subject: str | None, query: Query, limit: int) -> list[ChunkHit]:
        return self._hybrid(subject, query, lambda _: True)[:limit]

    async def unindexed(self, model: str, limit: int) -> list[Memory]:
        def pending(record: Memory) -> bool:
            entry = self.chunks.get(record.id)
            return entry is None or entry.model != model or record.updated_at > entry.at

        found = [r for r in self.records.values() if r.active and pending(r)]
        return sorted(found, key=lambda r: r.updated_at)[:limit]

    async def replace_chunks(
        self, memory_id: UUID, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        self.chunks[memory_id] = _Indexed(model, at, list(chunks))

    async def replace_documents(
        self, service: str, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        self.documents[service] = _Indexed(model, at, list(chunks))

    async def drop_stale_chunks(self, model: str) -> int:
        dropped = 0
        for memory_id, entry in list(self.chunks.items()):
            if entry.model != model or not self.records[memory_id].active:
                dropped += len(entry.chunks)
                del self.chunks[memory_id]
        for service, entry in list(self.documents.items()):
            if entry.model != model:
                dropped += len(entry.chunks)
                del self.documents[service]
        return dropped


@dataclass
class _Learning:
    requested_at: datetime
    status: str = "pendente"
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None


class InMemoryLearningQueue:
    """Fila do aprendizado sobre os pedidos (`learning_requests`) de um
    `InMemoryIncidentRepository`; o uso do modelo vai para `repository.usages`.
    """

    def __init__(self, repository: InMemoryIncidentRepository | None = None) -> None:
        self.repository = repository or InMemoryIncidentRepository()
        self.rows: dict[UUID, _Learning] = {}

    def _sync(self) -> None:
        """Pedido novo ou repetido (outro horário) volta a pendente com as tentativas zeradas."""
        for incident_id, at in self.repository.learning_requests.items():
            row = self.rows.get(incident_id)
            if row is None or row.requested_at != at:
                self.rows[incident_id] = _Learning(requested_at=at)

    async def claim(self, now: datetime) -> LearningJob | None:
        self._sync()
        pending = [
            (row.requested_at, i) for i, row in self.rows.items() if row.status == "pendente"
        ]
        if not pending:
            return None
        _, incident_id = min(pending)
        row = self.rows[incident_id]
        row.status, row.attempts, row.started_at = "em_andamento", row.attempts + 1, now
        return LearningJob(incident_id, row.attempts, now)

    async def stuck(self, started_before: datetime) -> list[LearningJob]:
        return [
            LearningJob(incident_id, row.attempts, row.started_at)
            for incident_id, row in self.rows.items()
            if row.status == "em_andamento" and row.started_at and row.started_at < started_before
        ]

    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        row = self.rows.get(job.incident_id)
        if row is None or row.status != "em_andamento":
            return
        self.repository.usages.extend((job.incident_id, u, now) for u in usages)
        if ok:
            row.status, row.finished_at, row.reason = "concluido", now, None
        elif row.attempts < LEARNING_ATTEMPTS:
            row.status, row.started_at, row.reason = "pendente", None, reason
        else:
            row.status, row.finished_at, row.reason = "falhou", now, reason

    async def failed(self) -> list[LearningFailure]:
        failures = [
            LearningFailure(
                incident_id,
                self._title(incident_id),
                row.reason or "",
                row.finished_at or row.requested_at,
            )
            for incident_id, row in self.rows.items()
            if row.status == "falhou"
        ]
        return sorted(failures, key=lambda f: f.at, reverse=True)

    def _title(self, incident_id: UUID) -> str:
        incident = self.repository.incidents.get(incident_id)
        return "" if incident is None else incident.title


class InMemoryStudyQueue:
    """Rodadas de estudo em memória. O estado é público para os testes inspecionarem."""

    def __init__(self) -> None:
        self.runs: list[StudyRun] = []
        self.studies: dict[str, ServiceStudy] = {}
        self.usages: list[tuple[int, LlmUsage]] = []  # (rodada, uso)

    def _replace(self, run: StudyRun) -> None:
        self.runs = [run if r.id == run.id else r for r in self.runs]

    def _current(self, run: StudyRun) -> StudyRun | None:
        return next((r for r in self.runs if r.id == run.id), None)

    async def request(self, mode: StudyMode, author: str, now: datetime) -> bool:
        pending = [r for r in self.runs if r.status is StudyStatus.PENDING]
        if mode is StudyMode.RELEARN and pending:
            self._replace(replace(pending[0], mode=mode, author=author, requested_at=now))
            return True
        running = any(r.status is StudyStatus.RUNNING for r in self.runs)
        if mode is not StudyMode.RELEARN and (pending or running):
            return False
        next_id = max((r.id for r in self.runs), default=0) + 1
        self.runs.append(StudyRun(next_id, mode, author, StudyStatus.PENDING, now))
        return True

    async def claim(self, now: datetime) -> StudyRun | None:
        pending = [r for r in self.runs if r.status is StudyStatus.PENDING]
        oldest = min(pending, key=lambda r: (r.requested_at, r.id), default=None)
        if oldest is None:
            return None
        claimed = replace(oldest, status=StudyStatus.RUNNING, started_at=now)
        self._replace(claimed)
        return claimed

    async def stuck(self, started_before: datetime) -> list[StudyRun]:
        return [
            r
            for r in self.runs
            if r.status is StudyStatus.RUNNING and r.started_at and r.started_at < started_before
        ]

    async def service_order(self, services: Sequence[str], mode: StudyMode) -> list[str]:
        del mode  # todas as rodadas usam a mesma ordem
        first: list[str] = []
        studied: list[tuple[datetime, str]] = []
        for service in dict.fromkeys(services):
            study = self.studies.get(service)
            if study is None or not study.ok or study.studied_at is None:
                first.append(service)
            else:
                studied.append((study.studied_at, service))
        return first + [service for _, service in sorted(studied)]

    async def service_done(
        self, run: StudyRun, study: ServiceStudy, usages: Sequence[LlmUsage]
    ) -> None:
        self.studies[study.service] = study
        self.usages.extend((run.id, u) for u in usages)

    async def finish(
        self, run: StudyRun, status: StudyStatus, reason: str | None, now: datetime
    ) -> None:
        current = self._current(run)
        if current is None or current.status is not StudyStatus.RUNNING:
            return
        tokens = sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens
            for run_id, u in self.usages
            if run_id == run.id
        )
        self._replace(
            replace(current, status=status, reason=reason, finished_at=now, tokens=tokens)
        )

    async def last(self) -> StudyRun | None:
        return max(self.runs, key=lambda r: (r.requested_at, r.id), default=None)

    async def services(self) -> list[ServiceStudy]:
        return sorted(self.studies.values(), key=lambda s: s.service)
