"""RAG da memória viva: indexar dossiês, episódios, notas e a documentação dos repositórios em
trechos com embedding, e montar as consultas da busca híbrida.

Contrato da memória com RAG: os corpos são do grolla-dominio.
"""

from dataclasses import dataclass

from grolla.application.clock import Clock
from grolla.application.investigation import ChangeHistory
from grolla.application.memory_store import MemoryStore, Query, TextEmbedder


@dataclass(frozen=True)
class RagPolicy:
    """Limites da indexação; o modelo é o do `TextEmbedder` (`memory.rag.size` no YAML)."""

    chunk_chars: int = 1200  # tamanho máximo de um trecho
    max_doc_files: int = 30  # .md indexados por serviço
    max_doc_bytes: int = 300_000  # tamanho total da documentação indexada por serviço
    batch: int = 50  # registros por rodada do job de indexação


async def index_memory(
    *, memory: MemoryStore, embedder: TextEmbedder, clock: Clock, policy: RagPolicy
) -> int:
    """Indexa até `policy.batch` registros pendentes (novos, mudados ou de outro modelo) e apaga
    os trechos velhos; devolve quantos registros indexou.
    """
    raise NotImplementedError


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
    raise NotImplementedError


async def query_for(embedder: TextEmbedder | None, text: str) -> Query:
    """A consulta da busca: com vetor quando há embedder; falha do embedder vira consulta só
    textual (a investigação nunca para por causa do RAG).
    """
    raise NotImplementedError
