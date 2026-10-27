"""Port da memória do agente: onde a base de conhecimento, os episódios e as notas vivem.

Fica num módulo próprio porque as ferramentas da investigação (`recall`, `remember`) e os casos
de uso da memória usam o mesmo port. `memory_for` também fica aqui, para a análise, a validação e
o chat a usarem sem import circular com `grolla.application.memory` (que reexporta).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from grolla.domain.incidents import Alert, Incident
from grolla.domain.memory import (
    Chunk,
    ChunkHit,
    Memory,
    MemoryContext,
    MemoryVersion,
    SubjectKind,
)

EmbeddingKind = Literal["query", "passage"]


class EmbeddingError(Exception):
    """O modelo de embeddings falhou ou não está disponível."""


class TextEmbedder(Protocol):
    """Embeddings de texto, gerados localmente. O mesmo modelo serve consulta e trecho."""

    @property
    def model(self) -> str:
        """O nome do modelo; trechos de outro modelo não são comparados."""
        ...

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        """Um vetor normalizado por texto, na mesma ordem; levanta EmbeddingError."""
        ...


@dataclass(frozen=True)
class Query:
    """Uma consulta à memória; sem vetor, só a busca textual (RAG desligado)."""

    text: str
    vector: Sequence[float] | None = None
    model: str | None = None  # o modelo do vetor: só trechos desse modelo entram


Embedded = tuple[Chunk, Sequence[float]]


@dataclass(frozen=True)
class SubjectSummary:
    subject: str
    kind: SubjectKind
    records: int  # ativos
    updated_at: datetime
    studied_at: datetime | None  # último estudo do serviço; None para os outros tipos


class MemoryStore(Protocol):
    async def context_for(
        self,
        services: Sequence[str],
        alert_names: Sequence[str],
        text: str,
        query: Query | None = None,
    ) -> MemoryContext:
        """A memória para investigar um incidente dos `services`.

        - dossiês: o do ambiente, o de cada serviço e o de cada integração cujo nome aparece
          no dossiê de um desses serviços;
        - notas: ativas e não consolidadas desses assuntos;
        - episódios: até EPISODE_LIMIT ativos, primeiro os do mesmo serviço com uma das
          `alert_names` no texto, depois por relevância da busca textual de `text`.
        """
        ...

    async def search(self, subject: str | None, query: str, limit: int) -> list[Memory]:
        """Registros ativos que casam com a busca textual; confirmados e recentes primeiro.

        `subject` None busca em todos os assuntos.
        """
        ...

    async def get(self, memory_id: UUID) -> Memory | None: ...

    async def dossier(self, subject: str) -> Memory | None:
        """O dossiê do assunto, ativo ou esquecido; None se nunca existiu."""
        ...

    async def episode_of(self, incident_id: UUID, subject: str) -> Memory | None: ...

    async def all_active(self) -> list[Memory]: ...

    async def save(self, records: Sequence[tuple[Memory, MemoryVersion | None]]) -> None:
        """Grava (insere ou atualiza pelo id) os registros e as versões numa transação."""
        ...

    async def subjects(self) -> list[SubjectSummary]:
        """Assuntos com algum registro: ambiente primeiro, depois serviços e integrações."""
        ...

    async def of_subject(self, subject: str) -> list[Memory]:
        """Todos os registros do assunto, inclusive esquecidos; dossiê, episódios e notas."""
        ...

    async def versions(self, memory_id: UUID) -> list[MemoryVersion]:
        """Mais recentes primeiro."""
        ...

    # ---- Busca com RAG (memória com RAG) ----
    # Com `query.vector`, `context_for` ordena os episódios, depois do critério determinístico,
    # por fusão de rankings (RRF) do vetor e do texto, e preenche `documents` com até
    # DOCUMENT_LIMIT trechos de documentação dos `services`.

    async def search_chunks(self, subject: str | None, query: Query, limit: int) -> list[ChunkHit]:
        """Busca híbrida (RRF de vetor e texto) nos trechos de registros ativos e de documentos
        do modelo da query; `subject` None busca em todos. Sem vetor, só a textual.
        """
        ...

    async def unindexed(self, model: str, limit: int) -> list[Memory]:
        """Registros ativos sem trechos deste modelo ou mudados depois da última indexação."""
        ...

    async def replace_chunks(
        self, memory_id: UUID, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        """Troca os trechos do registro pelos dados, indexados em `at`."""
        ...

    async def replace_documents(
        self, service: str, model: str, chunks: Sequence[Embedded], at: datetime
    ) -> None:
        """Troca todos os trechos de documentação do serviço (arquivo removido some)."""
        ...

    async def drop_stale_chunks(self, model: str) -> int:
        """Apaga trechos de registros esquecidos e de outro modelo; devolve quantos."""
        ...


async def query_for(embedder: TextEmbedder | None, text: str) -> Query:
    """A consulta da busca: com vetor quando há embedder. Qualquer falha do embedder vira
    consulta só textual (a investigação nunca para por causa do RAG).
    """
    if embedder is None:
        return Query(text)
    try:
        [vector] = await embedder.embed([text], "query")
    except Exception:
        return Query(text, model=embedder.model)
    return Query(text, vector, embedder.model)


async def memory_for(
    memory: MemoryStore | None,
    incident: Incident,
    alerts: Sequence[Alert],
    embedder: TextEmbedder | None = None,
) -> MemoryContext:
    """A memória para investigar o incidente; vazia sem memória ligada. Com `embedder`, a busca
    é híbrida e traz trechos da documentação.
    """
    if memory is None:
        return MemoryContext()
    names = list(dict.fromkeys(alert.rule_name for alert in alerts))
    text = " ".join([incident.title, *(alert.summary for alert in alerts)])
    query = await query_for(embedder, text)
    context = await memory.context_for(incident.services, names, text, query)
    # Uma análise atrasada pode rodar depois do episódio do próprio incidente: ele não é "já vi".
    episodes = tuple(e for e in context.episodes if e.incident_id != incident.id)
    return replace(context, episodes=episodes)
