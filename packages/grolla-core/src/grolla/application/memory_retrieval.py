"""RAG da memória viva: indexar dossiês, episódios, notas e a documentação dos repositórios em
trechos com embedding, e montar as consultas da busca híbrida.

`query_for` mora em `grolla.application.memory_store` (o `memory_for` usa) e é reexportado aqui.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from grolla.application.clock import Clock
from grolla.application.investigation import ChangeHistory
from grolla.application.memory_store import Embedded, MemoryStore, TextEmbedder, query_for
from grolla.domain.memory import Chunk, ChunkOrigin, Memory, MemoryKind, chunk_text

__all__ = ["RagPolicy", "index_documents", "index_memory", "memory_chunks", "query_for"]


@dataclass(frozen=True)
class RagPolicy:
    """Limites da indexação; o modelo é o do `TextEmbedder` (`memory.rag.size` no YAML)."""

    chunk_chars: int = 1200  # tamanho máximo de um trecho
    max_doc_files: int = 30  # .md indexados por serviço
    max_doc_bytes: int = 300_000  # tamanho total da documentação indexada por serviço
    batch: int = 50  # registros por rodada do job de indexação


def _passage(chunk: Chunk) -> str:
    """O texto que vira embedding: a seção e o trecho."""
    return f"{chunk.heading}\n{chunk.text}" if chunk.heading else chunk.text


async def _embedded(embedder: TextEmbedder, chunks: Sequence[Chunk]) -> list[Embedded]:
    if not chunks:
        return []
    vectors = await embedder.embed([_passage(c) for c in chunks], "passage")
    return list(zip(chunks, vectors, strict=True))


def memory_chunks(record: Memory, limit: int) -> list[Chunk]:
    """Os trechos de um registro, sempre ao menos um; o título do episódio e da nota entra no
    texto de cada trecho.
    """
    sections = chunk_text(record.body, limit) or [("", record.title)]
    chunks: list[Chunk] = []
    titled = record.kind is not MemoryKind.DOSSIER
    for heading, text in sections:
        full = f"{record.title}\n{text}" if titled and not text.startswith(record.title) else text
        chunk = Chunk(
            ChunkOrigin.MEMORY, record.subject, heading, full, memory_id=record.id, kind=record.kind
        )
        chunks.append(chunk)
    return chunks


async def index_memory(
    *, memory: MemoryStore, embedder: TextEmbedder, clock: Clock, policy: RagPolicy
) -> int:
    """Indexa até `policy.batch` registros pendentes (novos, mudados ou de outro modelo), com um
    lote só de embeddings, e apaga os trechos velhos; devolve quantos registros indexou.
    Levanta EmbeddingError.

    Cada registro fica indexado na data da versão lida (`updated_at`), não no relógio: uma
    edição feita durante a rodada continua pendente para a próxima.
    """
    del clock  # a data da indexação é a da versão indexada
    model = embedder.model
    records = await memory.unindexed(model, policy.batch)
    by_record = [memory_chunks(record, policy.chunk_chars) for record in records]
    embedded = await _embedded(embedder, [chunk for chunks in by_record for chunk in chunks])
    start = 0
    for record, chunks in zip(records, by_record, strict=True):
        mine = embedded[start : start + len(chunks)]
        await memory.replace_chunks(record.id, model, mine, record.updated_at)
        start += len(chunks)
    await memory.drop_stale_chunks(model)
    return len(records)


async def index_documents(  # noqa: PLR0913 — a indexação liga repositório, memória e modelo
    *,
    service: str,
    memory: MemoryStore,
    changes: ChangeHistory,
    embedder: TextEmbedder,
    clock: Clock,
    policy: RagPolicy,
) -> int:
    """Indexa os `.md` do repositório do serviço, trocando os trechos de documentação dele;
    devolve quantos trechos gravou. Levanta ToolUnavailableError ou EmbeddingError.
    """
    documents = await changes.documents(service, policy.max_doc_files, policy.max_doc_bytes)
    chunks = [
        Chunk(ChunkOrigin.DOCUMENT, service, heading, text, path=doc.path, url=doc.url)
        for doc in documents
        for heading, text in chunk_text(doc.content, policy.chunk_chars)
    ]
    embedded = await _embedded(embedder, chunks)
    await memory.replace_documents(service, embedder.model, embedded, clock.now())
    return len(embedded)
