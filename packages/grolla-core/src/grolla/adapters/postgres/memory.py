"""Memória viva no PostgreSQL (migração 0006): registros com busca textual em português,
versões, a fila do aprendizado e as rodadas de estudo.

A tabela `learnings` é a fila do aprendizado e usa os mesmos status das rodadas de estudo.
Os tokens de uma rodada são a soma do `llm_usage` gravado com o id dela.

Os trechos (migração 0007) guardam o vetor em `vector` do pgvector, enviado como literal de
texto (`[0.1,0.2]`) com CAST, e o `tsvector` do texto. A busca híbrida junta os K mais
próximos pelo vetor (distância de cosseno exata, sem índice ANN) e os K melhores por `ts_rank`
com a fusão de rankings (RRF): `score = soma de 1/(60 + posição)`.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa

from grolla.adapters.postgres.analysis import record_usages
from grolla.adapters.postgres.database import PostgresDatabase
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
from grolla.application.memory_store import Embedded, Query, SubjectSummary
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
    Source,
    SourceKind,
    SubjectKind,
)

MEMORY_COLUMNS = (
    "id, kind, subject, subject_kind, title, body, confidence, source_kind, source_author,"
    " source_ref, incident_id, active, consolidated, noise, created_at, updated_at"
)
SELECT_MEMORIES = f"SELECT {MEMORY_COLUMNS} FROM memories"  # noqa: S608 — colunas fixas
# Ordem da página: dossiê, episódios, notas.
KIND_ORDER = "CASE kind WHEN 'dossie' THEN 0 WHEN 'episodio' THEN 1 ELSE 2 END"

DOSSIERS = sa.text(
    f"{SELECT_MEMORIES} WHERE kind = :dossier AND active"
    " AND subject = ANY(CAST(:subjects AS text[]))"
)
# Integrações citadas no dossiê de algum dos serviços pedidos.
CITED_INTEGRATIONS = sa.text(
    f"{SELECT_MEMORIES} AS i WHERE i.kind = :dossier AND i.active"  # noqa: S608 — colunas fixas
    " AND i.subject_kind = :integration AND EXISTS (SELECT 1 FROM memories AS s"
    " WHERE s.kind = :dossier AND s.active AND s.subject = ANY(CAST(:services AS text[]))"
    " AND strpos(lower(s.body), lower(i.subject)) > 0)"
    " ORDER BY i.subject"
)
PENDING_NOTES = sa.text(
    f"{SELECT_MEMORIES} WHERE kind = :note AND active AND NOT consolidated"
    " AND subject = ANY(CAST(:subjects AS text[])) ORDER BY created_at, id"
)
# Mesmo serviço com a regra de alerta no texto, depois mesmo serviço, depois relevância;
# sem texto, os mais recentes do serviço.
SIMILAR_EPISODES = sa.text(
    f"SELECT {MEMORY_COLUMNS} FROM memories,"  # noqa: S608 — colunas fixas
    " websearch_to_tsquery('portuguese', :text) AS query"
    " WHERE kind = :episode AND active"
    " AND (subject = ANY(CAST(:services AS text[])) OR search @@ query)"
    " ORDER BY (subject = ANY(CAST(:services AS text[])) AND EXISTS ("
    "SELECT 1 FROM unnest(CAST(:alerts AS text[])) AS alert WHERE alert <> ''"
    " AND strpos(lower(title || ' ' || body), lower(alert)) > 0)) DESC,"
    " subject = ANY(CAST(:services AS text[])) DESC,"
    " ts_rank(search, query) DESC, updated_at DESC, id"
    " LIMIT :limit"
)
# Com vetor: o determinístico continua primeiro; depois o RRF do vetor (melhor trecho de cada
# episódio) e do texto. Episódio de outro serviço só entra se um dos dois o trouxer no top K.
RRF = 60
HYBRID_EPISODES = sa.text(
    "WITH q AS (SELECT websearch_to_tsquery('portuguese', :text) AS tsq),"  # noqa: S608 — idem
    " vec AS (SELECT memory_id, row_number() OVER (ORDER BY distance, memory_id) AS pos"
    " FROM (SELECT c.memory_id, min(c.embedding <=> CAST(:vector AS vector)) AS distance"
    " FROM memory_chunks AS c JOIN memories AS m ON m.id = c.memory_id"
    " WHERE c.model = :model AND m.kind = :episode AND m.active GROUP BY c.memory_id) AS d"
    " ORDER BY pos LIMIT :k),"
    " txt AS (SELECT m.id AS memory_id, row_number() OVER ("
    "ORDER BY ts_rank(m.search, q.tsq) DESC, m.updated_at DESC, m.id) AS pos"
    " FROM memories AS m, q WHERE m.kind = :episode AND m.active AND m.search @@ q.tsq"
    " ORDER BY pos LIMIT :k),"
    f" fused AS (SELECT memory_id, sum(1.0 / ({RRF} + pos)) AS score"
    " FROM (SELECT * FROM vec UNION ALL SELECT * FROM txt) AS r GROUP BY memory_id)"
    f" SELECT {MEMORY_COLUMNS} FROM memories LEFT JOIN fused ON fused.memory_id = memories.id"
    " WHERE kind = :episode AND active"
    " AND (subject = ANY(CAST(:services AS text[])) OR fused.memory_id IS NOT NULL)"
    " ORDER BY (subject = ANY(CAST(:services AS text[])) AND EXISTS ("
    "SELECT 1 FROM unnest(CAST(:alerts AS text[])) AS alert WHERE alert <> ''"
    " AND strpos(lower(title || ' ' || body), lower(alert)) > 0)) DESC,"
    " coalesce(fused.score, 0) DESC, subject = ANY(CAST(:services AS text[])) DESC,"
    " updated_at DESC, id"
    " LIMIT :limit"
)
CHUNK_COLUMNS = "id, origin, subject, memory_id, kind, path, url, heading, text"
# Trechos de documentos e de registros ativos do modelo, filtrados por assunto e origem. Sem
# vetor, `vec` fica vazio e sobra o ranking textual.
SEARCH_CHUNKS = sa.text(
    "WITH base AS (SELECT c.* FROM memory_chunks AS c"  # noqa: S608 — colunas e constantes fixas
    " LEFT JOIN memories AS m ON m.id = c.memory_id"
    " WHERE (CAST(:model AS text) IS NULL OR c.model = :model)"
    " AND (c.origin = :document OR m.active)"
    " AND (CAST(:subjects AS text[]) IS NULL OR c.subject = ANY(CAST(:subjects AS text[])))"
    " AND (CAST(:origin AS text) IS NULL OR c.origin = :origin)),"
    " q AS (SELECT websearch_to_tsquery('portuguese', :text) AS tsq),"
    " vec AS (SELECT id, row_number() OVER ("
    "ORDER BY embedding <=> CAST(:vector AS vector), id) AS pos"
    " FROM base WHERE CAST(:vector AS text) IS NOT NULL ORDER BY pos LIMIT :k),"
    " txt AS (SELECT id, row_number() OVER (ORDER BY ts_rank(search, q.tsq) DESC, id) AS pos"
    " FROM base, q WHERE search @@ q.tsq ORDER BY pos LIMIT :k),"
    f" fused AS (SELECT id, CAST(sum(1.0 / ({RRF} + pos)) AS float8) AS score"
    " FROM (SELECT * FROM vec UNION ALL SELECT * FROM txt) AS r GROUP BY id)"
    f" SELECT {CHUNK_COLUMNS}, fused.score FROM fused JOIN memory_chunks USING (id)"
    " ORDER BY fused.score DESC, id LIMIT :limit"
)
# Sem trecho deste modelo, ou mudado depois da última indexação dele.
UNINDEXED = sa.text(
    f"SELECT {MEMORY_COLUMNS} FROM memories AS m, LATERAL ("  # noqa: S608 — colunas fixas
    "SELECT max(c.indexed_at) AS indexed_at FROM memory_chunks AS c"
    " WHERE c.memory_id = m.id AND c.model = :model) AS i"
    " WHERE m.active AND (i.indexed_at IS NULL OR m.updated_at > i.indexed_at)"
    " ORDER BY m.updated_at, m.id LIMIT :limit"
)
INSERT_CHUNK = sa.text(
    "INSERT INTO memory_chunks (origin, subject, memory_id, kind, path, url, heading, text,"
    " model, embedding, indexed_at) VALUES (:origin, :subject, :memory_id, :kind, :path, :url,"
    " :heading, :text, :model, CAST(:embedding AS vector), :indexed_at)"
)
DROP_STALE_CHUNKS = sa.text(
    "DELETE FROM memory_chunks AS c WHERE c.model <> :model OR EXISTS ("
    "SELECT 1 FROM memories AS m WHERE m.id = c.memory_id AND NOT m.active)"
)
SEARCH = sa.text(
    f"{SELECT_MEMORIES} WHERE active"
    " AND search @@ websearch_to_tsquery('portuguese', :query)"
    " AND (CAST(:subject AS text) IS NULL OR subject = :subject)"
    " ORDER BY confidence = :confirmed DESC, updated_at DESC, id LIMIT :limit"
)
UPSERT_MEMORY = sa.text(
    f"INSERT INTO memories ({MEMORY_COLUMNS}) VALUES (:id, :kind, :subject, :subject_kind,"  # noqa: S608 — colunas fixas
    " :title, :body, :confidence, :source_kind, :source_author, :source_ref, :incident_id,"
    " :active, :consolidated, :noise, :created_at, :updated_at)"
    " ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, subject = EXCLUDED.subject,"
    " subject_kind = EXCLUDED.subject_kind, title = EXCLUDED.title, body = EXCLUDED.body,"
    " confidence = EXCLUDED.confidence, source_kind = EXCLUDED.source_kind,"
    " source_author = EXCLUDED.source_author, source_ref = EXCLUDED.source_ref,"
    " incident_id = EXCLUDED.incident_id, active = EXCLUDED.active,"
    " consolidated = EXCLUDED.consolidated, noise = EXCLUDED.noise,"
    " created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at"
)
SUBJECTS = sa.text(
    "SELECT g.subject, g.kind, g.records, g.updated_at,"
    " CASE WHEN g.kind = :service THEN s.studied_at END AS studied_at"
    " FROM (SELECT subject, subject_kind AS kind, count(*) FILTER (WHERE active) AS records,"
    " max(updated_at) AS updated_at FROM memories GROUP BY subject, subject_kind) AS g"
    " LEFT JOIN service_studies AS s ON s.service = g.subject"
    " ORDER BY CASE g.kind WHEN :environment THEN 0 WHEN :service THEN 1 ELSE 2 END,"
    " g.subject"
)

# SKIP LOCKED: workers em paralelo pegam pedidos diferentes, sem esperar um ao outro.
CLAIM_LEARNING = sa.text(
    "UPDATE learnings SET status = :running, started_at = :now, attempts = attempts + 1"
    " WHERE incident_id = (SELECT incident_id FROM learnings"
    " WHERE status = :pending ORDER BY requested_at, incident_id"
    " LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING incident_id, attempts, started_at"
)

RUN_COLUMNS = (
    "id, mode, author, status, requested_at, started_at, finished_at, reason,"
    " (SELECT coalesce(sum(u.input_tokens + u.output_tokens + u.cache_read_tokens"
    " + u.cache_creation_tokens), 0) FROM llm_usage AS u"
    " WHERE u.study_run_id = study_runs.id) AS tokens"
)
# Sem conflito de alvo: o índice único parcial deixa uma rodada pendente só.
REQUEST_STUDY = sa.text(
    "INSERT INTO study_runs (mode, author, status, requested_at)"
    " SELECT :mode, :author, :pending, :now WHERE NOT EXISTS ("
    "SELECT 1 FROM study_runs WHERE status IN (:pending, :running))"
    " ON CONFLICT DO NOTHING"
)
# Reaprender substitui a pendente (ou entra como pendente, mesmo com uma rodando).
REQUEST_RELEARN = sa.text(
    "INSERT INTO study_runs (mode, author, status, requested_at)"
    " VALUES (:mode, :author, :pending, :now)"
    " ON CONFLICT ((true)) WHERE status = 'pendente' DO UPDATE SET mode = EXCLUDED.mode,"
    " author = EXCLUDED.author, requested_at = EXCLUDED.requested_at"
)
CLAIM_STUDY = sa.text(
    "UPDATE study_runs SET status = :running, started_at = :now"  # noqa: S608 — colunas fixas
    " WHERE id = (SELECT id FROM study_runs WHERE status = :pending"
    " ORDER BY requested_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    f" RETURNING {RUN_COLUMNS}"
)
# Uma falha não apaga quando o serviço foi estudado com sucesso pela última vez.
UPSERT_SERVICE_STUDY = sa.text(
    "INSERT INTO service_studies (service, studied_at, ok, reason)"
    " VALUES (:service, :studied_at, :ok, :reason)"
    " ON CONFLICT (service) DO UPDATE SET"
    " studied_at = coalesce(EXCLUDED.studied_at, service_studies.studied_at),"
    " ok = EXCLUDED.ok, reason = EXCLUDED.reason"
)


def _memory(row: sa.Row[Any]) -> Memory:
    return Memory(
        id=row.id,
        kind=MemoryKind(row.kind),
        subject=row.subject,
        subject_kind=SubjectKind(row.subject_kind),
        title=row.title,
        body=row.body,
        confidence=Confidence(row.confidence),
        source=Source(
            kind=SourceKind(row.source_kind), author=row.source_author, ref=row.source_ref
        ),
        created_at=row.created_at,
        updated_at=row.updated_at,
        active=row.active,
        consolidated=row.consolidated,
        noise=row.noise,
        incident_id=row.incident_id,
    )


def _memory_params(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "kind": memory.kind.value,
        "subject": memory.subject,
        "subject_kind": memory.subject_kind.value,
        "title": memory.title,
        "body": memory.body,
        "confidence": memory.confidence.value,
        "source_kind": memory.source.kind.value,
        "source_author": memory.source.author,
        "source_ref": memory.source.ref,
        "incident_id": memory.incident_id,
        "active": memory.active,
        "consolidated": memory.consolidated,
        "noise": memory.noise,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def _version(row: sa.Row[Any]) -> MemoryVersion:
    return MemoryVersion(
        memory_id=row.memory_id,
        at=row.at,
        author=row.author,
        reason=row.reason,
        body=row.body,
        active=row.active,
    )


def _vector(values: Sequence[float]) -> str:
    """Literal de texto do pgvector: `[0.1,0.2]`."""
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _chunk_hit(row: sa.Row[Any]) -> ChunkHit:
    chunk = Chunk(
        origin=ChunkOrigin(row.origin),
        subject=row.subject,
        heading=row.heading,
        text=row.text,
        memory_id=row.memory_id,
        kind=None if row.kind is None else MemoryKind(row.kind),
        path=row.path,
        url=row.url,
    )
    return ChunkHit(chunk=chunk, score=row.score)


def _chunk_params(
    chunks: Sequence[Embedded], model: str, at: datetime, memory_id: UUID | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "origin": chunk.origin.value,
            "subject": chunk.subject,
            "memory_id": memory_id,
            "kind": None if chunk.kind is None else chunk.kind.value,
            "path": chunk.path,
            "url": chunk.url,
            "heading": chunk.heading,
            "text": chunk.text,
            "model": model,
            "embedding": _vector(vector),
            "indexed_at": at,
        }
        for chunk, vector in chunks
    ]


def _candidates(limit: int) -> int:
    """Quantos candidatos cada ranking (vetor e texto) leva para a fusão."""
    return max(limit * 4, 20)


class PostgresMemoryStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def _memories(self, statement: sa.TextClause, **params: Any) -> list[Memory]:
        async with self._engine.connect() as connection:
            result = await connection.execute(statement, params)
            return [_memory(row) for row in result]

    async def _memory(self, statement: sa.TextClause, **params: Any) -> Memory | None:
        found = await self._memories(statement, **params)
        return found[0] if found else None

    async def context_for(
        self,
        services: Sequence[str],
        alert_names: Sequence[str],
        text: str,
        query: Query | None = None,
    ) -> MemoryContext:
        services = list(services)
        vector = None if query is None or query.vector is None else _vector(query.vector)
        async with self._engine.connect() as connection:
            result = await connection.execute(
                DOSSIERS,
                {"dossier": MemoryKind.DOSSIER.value, "subjects": [ENVIRONMENT, *services]},
            )
            by_subject = {row.subject: _memory(row) for row in result}
            result = await connection.execute(
                CITED_INTEGRATIONS,
                {
                    "dossier": MemoryKind.DOSSIER.value,
                    "integration": SubjectKind.INTEGRATION.value,
                    "services": services,
                },
            )
            integrations = [_memory(row) for row in result]
            subjects = [ENVIRONMENT, *services, *(i.subject for i in integrations)]
            result = await connection.execute(
                PENDING_NOTES, {"note": MemoryKind.NOTE.value, "subjects": subjects}
            )
            notes = tuple(_memory(row) for row in result)
            params: dict[str, Any] = {
                "episode": MemoryKind.EPISODE.value,
                "services": services,
                "alerts": list(alert_names),
                "text": text,
                "limit": EPISODE_LIMIT,
            }
            if query is not None and vector is not None:
                params |= {
                    "vector": vector,
                    "model": query.model,
                    "k": _candidates(EPISODE_LIMIT),
                }
            result = await connection.execute(
                SIMILAR_EPISODES if vector is None else HYBRID_EPISODES, params
            )
            episodes = tuple(_memory(row) for row in result)
        documents: list[ChunkHit] = []
        if query is not None and vector is not None and services:
            documents = await self._search_chunks(
                query, DOCUMENT_LIMIT, subjects=services, origin=ChunkOrigin.DOCUMENT
            )
        # Ambiente, serviços na ordem pedida, integrações por nome.
        ordered = [
            by_subject[s] for s in dict.fromkeys([ENVIRONMENT, *services]) if s in by_subject
        ]
        return MemoryContext(
            dossiers=(*ordered, *(i for i in integrations if i.subject not in by_subject)),
            notes=notes,
            episodes=episodes,
            documents=tuple(documents),
        )

    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]:
        return await self._memories(
            SEARCH,
            query=query,
            subject=subject,
            confirmed=Confidence.CONFIRMED.value,
            limit=limit,
        )

    async def get(self, memory_id: UUID) -> Memory | None:
        return await self._memory(sa.text(f"{SELECT_MEMORIES} WHERE id = :id"), id=memory_id)

    async def dossier(self, subject: str) -> Memory | None:
        return await self._memory(
            sa.text(f"{SELECT_MEMORIES} WHERE kind = :dossier AND subject = :subject"),
            dossier=MemoryKind.DOSSIER.value,
            subject=subject,
        )

    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None:
        return await self._memory(
            sa.text(
                f"{SELECT_MEMORIES} WHERE kind = :episode AND incident_id = :incident_id"
                " AND subject = :subject"
            ),
            episode=MemoryKind.EPISODE.value,
            incident_id=incident_id,
            subject=subject,
        )

    async def all_active(self) -> list[Memory]:
        return await self._memories(
            sa.text(
                f"{SELECT_MEMORIES} WHERE active ORDER BY subject, {KIND_ORDER}, created_at, id"
            )
        )

    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        if not records:
            return
        versions = [version for _, version in records if version is not None]
        async with self._engine.begin() as connection:
            await connection.execute(
                UPSERT_MEMORY, [_memory_params(memory) for memory, _ in records]
            )
            if versions:
                await connection.execute(
                    sa.text(
                        "INSERT INTO memory_versions (memory_id, at, author, reason, body,"
                        " active) VALUES (:memory_id, :at, :author, :reason, :body, :active)"
                    ),
                    [
                        {
                            "memory_id": version.memory_id,
                            "at": version.at,
                            "author": version.author,
                            "reason": version.reason,
                            "body": version.body,
                            "active": version.active,
                        }
                        for version in versions
                    ],
                )

    async def subjects(self) -> list[SubjectSummary]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                SUBJECTS,
                {
                    "environment": SubjectKind.ENVIRONMENT.value,
                    "service": SubjectKind.SERVICE.value,
                },
            )
            return [
                SubjectSummary(
                    subject=row.subject,
                    kind=SubjectKind(row.kind),
                    records=row.records,
                    updated_at=row.updated_at,
                    studied_at=row.studied_at,
                )
                for row in result
            ]

    async def of_subject(self, subject: str) -> list[Memory]:
        return await self._memories(
            sa.text(
                f"{SELECT_MEMORIES} WHERE subject = :subject"
                f" ORDER BY {KIND_ORDER}, created_at DESC, id"
            ),
            subject=subject,
        )

    async def versions(self, memory_id: UUID) -> list[MemoryVersion]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT memory_id, at, author, reason, body, active FROM memory_versions"
                    " WHERE memory_id = :id ORDER BY at DESC, id DESC"
                ),
                {"id": memory_id},
            )
            return [_version(row) for row in result]

    # ---- memória com RAG ----

    async def _search_chunks(
        self,
        query: Query,
        limit: int,
        *,
        subjects: Sequence[str] | None,
        origin: ChunkOrigin | None = None,
    ) -> list[ChunkHit]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                SEARCH_CHUNKS,
                {
                    "model": query.model,
                    "document": ChunkOrigin.DOCUMENT.value,
                    "subjects": None if subjects is None else list(subjects),
                    "origin": None if origin is None else origin.value,
                    "text": query.text,
                    "vector": None if query.vector is None else _vector(query.vector),
                    "k": _candidates(limit),
                    "limit": limit,
                },
            )
            return [_chunk_hit(row) for row in result]

    async def search_chunks(self, subject: str | None, query: Query, limit: int) -> list[ChunkHit]:
        return await self._search_chunks(
            query, limit, subjects=None if subject is None else [subject]
        )

    async def unindexed(self, model: str, limit: int) -> list[Memory]:
        return await self._memories(UNINDEXED, model=model, limit=limit)

    async def replace_chunks(
        self, memory_id: UUID, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text("DELETE FROM memory_chunks WHERE memory_id = :id"), {"id": memory_id}
            )
            if chunks:
                await connection.execute(
                    INSERT_CHUNK, _chunk_params(chunks, model, at, memory_id=memory_id)
                )

    async def replace_documents(
        self, service: str, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        document = ChunkOrigin.DOCUMENT.value
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "DELETE FROM memory_chunks WHERE origin = :document AND subject = :service"
                ),
                {"document": document, "service": service},
            )
            if chunks:
                params = _chunk_params(chunks, model, at)
                for param in params:  # o serviço e a origem são os da troca
                    param |= {"origin": document, "subject": service, "kind": None}
                await connection.execute(INSERT_CHUNK, params)

    async def drop_stale_chunks(self, model: str) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(DROP_STALE_CHUNKS, {"model": model})
            return result.rowcount


def _job(row: sa.Row[Any]) -> LearningJob:
    return LearningJob(
        incident_id=row.incident_id, attempts=row.attempts, started_at=row.started_at
    )


class PostgresLearningQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def claim(self, now: datetime) -> LearningJob | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_LEARNING,
                {
                    "running": StudyStatus.RUNNING.value,
                    "pending": StudyStatus.PENDING.value,
                    "now": now,
                },
            )
            row = result.one_or_none()
            return None if row is None else _job(row)

    async def stuck(self, started_before: datetime) -> list[LearningJob]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT incident_id, attempts, started_at FROM learnings"
                    " WHERE status = :running AND started_at < :before"
                    " ORDER BY started_at, incident_id"
                ),
                {"running": StudyStatus.RUNNING.value, "before": started_before},
            )
            return [_job(row) for row in result]

    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        if ok:
            status, finished_at = StudyStatus.DONE, now
        elif job.attempts < LEARNING_ATTEMPTS:
            status, finished_at = StudyStatus.PENDING, None  # mais uma tentativa
        else:
            status, finished_at = StudyStatus.FAILED, now
        async with self._engine.begin() as connection:
            updated = await connection.execute(
                sa.text(
                    "UPDATE learnings SET status = :status, finished_at = :finished_at,"
                    " reason = :reason WHERE incident_id = :incident_id AND status = :running"
                ),
                {
                    "incident_id": job.incident_id,
                    "running": StudyStatus.RUNNING.value,
                    "status": status.value,
                    "finished_at": finished_at,
                    "reason": reason,
                },
            )
            if updated.rowcount == 0:
                # Já encerrado (ex.: o reaper fechou como interrompido): o uso não conta duas vezes.
                return
            await record_usages(connection, job.incident_id, usages, now)

    async def failed(self) -> list[LearningFailure]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT l.incident_id, i.title, coalesce(l.reason, '') AS reason,"
                    " l.finished_at AS at FROM learnings AS l"
                    " JOIN incidents AS i ON i.id = l.incident_id"
                    " WHERE l.status = :failed ORDER BY l.finished_at DESC, l.incident_id"
                ),
                {"failed": StudyStatus.FAILED.value},
            )
            return [
                LearningFailure(
                    incident_id=row.incident_id, title=row.title, reason=row.reason, at=row.at
                )
                for row in result
            ]


def _run(row: sa.Row[Any]) -> StudyRun:
    return StudyRun(
        id=row.id,
        mode=StudyMode(row.mode),
        author=row.author,
        status=StudyStatus(row.status),
        requested_at=row.requested_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        tokens=int(row.tokens),
        reason=row.reason,
    )


class PostgresStudyQueue:
    def __init__(self, database: PostgresDatabase) -> None:
        self._engine = database.engine

    async def request(self, mode: StudyMode, author: str, now: datetime) -> bool:
        statement = REQUEST_RELEARN if mode is StudyMode.RELEARN else REQUEST_STUDY
        async with self._engine.begin() as connection:
            result = await connection.execute(
                statement,
                {
                    "mode": mode.value,
                    "author": author,
                    "pending": StudyStatus.PENDING.value,
                    "running": StudyStatus.RUNNING.value,
                    "now": now,
                },
            )
            return result.rowcount > 0

    async def claim(self, now: datetime) -> StudyRun | None:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                CLAIM_STUDY,
                {
                    "running": StudyStatus.RUNNING.value,
                    "pending": StudyStatus.PENDING.value,
                    "now": now,
                },
            )
            row = result.one_or_none()
            return None if row is None else _run(row)

    async def stuck(self, started_before: datetime) -> list[StudyRun]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    f"SELECT {RUN_COLUMNS} FROM study_runs"  # noqa: S608 — colunas fixas
                    " WHERE status = :running AND started_at < :before ORDER BY started_at, id"
                ),
                {"running": StudyStatus.RUNNING.value, "before": started_before},
            )
            return [_run(row) for row in result]

    async def service_order(
        self,
        services: Sequence[str],
        mode: StudyMode,  # noqa: ARG002 — a ordem é a mesma; FULL e RELEARN só estudam todos
    ) -> list[str]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT service, studied_at, ok FROM service_studies"
                    " WHERE service = ANY(CAST(:services AS text[]))"
                ),
                {"services": list(services)},
            )
            studied = {row.service: row for row in result}

        def key(item: tuple[int, str]) -> tuple[int, float, int]:
            index, service = item
            row = studied.get(service)
            if row is None or not row.ok or row.studied_at is None:
                return (0, 0.0, index)  # nunca estudado, falhou ou ficou sem orçamento
            return (1, row.studied_at.timestamp(), index)

        return [service for _, service in sorted(enumerate(services), key=key)]

    async def service_done(
        self, run: StudyRun, study: ServiceStudy, usages: Sequence[LlmUsage]
    ) -> None:
        at = study.studied_at or run.started_at or run.requested_at
        async with self._engine.begin() as connection:
            await connection.execute(
                UPSERT_SERVICE_STUDY,
                {
                    "service": study.service,
                    "studied_at": study.studied_at,
                    "ok": study.ok,
                    "reason": study.reason,
                },
            )
            await record_usages(connection, None, usages, at, study_run_id=run.id)

    async def finish(
        self, run: StudyRun, status: StudyStatus, reason: str | None, now: datetime
    ) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                sa.text(
                    "UPDATE study_runs SET status = :status, reason = :reason,"
                    " finished_at = :now WHERE id = :id AND status = :running"
                ),
                {
                    "id": run.id,
                    "running": StudyStatus.RUNNING.value,
                    "status": status.value,
                    "reason": reason,
                    "now": now,
                },
            )

    async def last(self) -> StudyRun | None:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    f"SELECT {RUN_COLUMNS} FROM study_runs"  # noqa: S608 — colunas fixas
                    " ORDER BY requested_at DESC, id DESC LIMIT 1"
                )
            )
            row = result.one_or_none()
            return None if row is None else _run(row)

    async def services(self) -> list[ServiceStudy]:
        async with self._engine.connect() as connection:
            result = await connection.execute(
                sa.text(
                    "SELECT service, studied_at, ok, reason FROM service_studies ORDER BY service"
                )
            )
            return [
                ServiceStudy(
                    service=row.service, studied_at=row.studied_at, ok=row.ok, reason=row.reason
                )
                for row in result
            ]
