"""Eval de recuperação da memória com RAG: busca textual sozinha contra a busca híbrida.

Sobe um Postgres com pgvector (testcontainers), grava um corpus fixo de episódios de um mesmo
serviço com distratores, indexa com o embedder real (modelo local, sem custo) e, para cada
consulta, compara a posição do episódio certo entre os episódios que a análise receberia. Não
chama LLM.

    GROLLA_RAG_CACHE=~/.cache/grolla-models uv run python evals/retrieval.py [--size small|large]

Aceite (spec memory-retrieval): toda consulta de paráfrase fica entre os 5 primeiros na híbrida,
e nenhuma consulta que a textual acerta (top 5) sai do top 5 na híbrida.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid5

from grolla.adapters.fastembed import DEFAULT_CACHE_DIR, FastembedTextEmbedder, RagSize
from grolla.adapters.postgres import PostgresDatabase, PostgresMemoryStore, migrate
from grolla.application.memory_retrieval import RagPolicy, index_memory, query_for
from grolla.application.memory_store import MemoryStore, Query, TextEmbedder
from grolla.domain.memory import (
    AGENT_AUTHOR,
    Confidence,
    Memory,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
)
from grolla.testing.builders import T0
from grolla.testing.clock import FakeClock
from testcontainers.community.postgres import PostgresContainer

SERVICE = "pedidos-api"
TOP = 5
NAMESPACE = UUID("0e7a1e00-0000-4000-8000-000000000000")
POSTGRES_IMAGE = "pgvector/pgvector:pg16"

# (chave, título, texto). Os primeiros são os casos certos das consultas e os mais antigos:
# a ordem por recência da busca textual não os favorece.
EPISODES = (
    (
        "pool",
        "pedidos-api lenta de madrugada pela rotina de reconciliação",
        "A rotina de reconciliação noturna abriu dezenas de sessões no banco e esgotou o limite "
        "de conexões; a API passou a falhar ao obter sessão. Pausar a rotina resolveu.",
    ),
    (
        "disco",
        "pedidos-api parou de gravar com o volume da EC2 lotado",
        "O volume raiz da máquina encheu por arquivos de log sem rotação; as gravações falharam "
        "até liberar espaço e ligar o logrotate.",
    ),
    (
        "tls",
        "pedidos-api sem falar com a bandeira: certificado do gateway venceu",
        "As chamadas ao gateway da bandeira foram recusadas porque o certificado do cliente "
        "passou da validade; renovar o certificado resolveu.",
    ),
    (
        "desconto",
        "pedidos-api com 5xx em GET /pedidos após deploy (desconto reintroduzido)",
        "KeyError: 'valor_desconto' em GET /pedidos logo após o deploy 20261027.1; a coluna não "
        "existe no banco. Rollback por revert na main resolveu.",
    ),
    (
        "rds",
        "pedidos-api fora com o grolla-lab-db parado",
        "O RDS grolla-lab-db foi parado manualmente; a API não conseguia abrir conexão até a "
        "instância voltar a available.",
    ),
    # Distratores: problemas do mesmo serviço com outras causas.
    ("d1", "pedidos-api com p99 alto por GC longo", "Pausas de coleta de lixo de 2 s na JVM."),
    ("d2", "pedidos-api com 429 do provedor de frete", "Limite de taxa do provedor de frete."),
    ("d3", "pedidos-api com erro de DNS interno", "Resolução de nomes falhando no VPC."),
    ("d4", "pedidos-api lenta após aumento de tráfego", "Pico de 3x no Black Friday; escalou."),
    ("d5", "pedidos-api com timeout na antifraude", "A antifraude-api respondeu em 8 s."),
    ("d6", "pedidos-api com fila de eventos atrasada", "Consumidor do Kafka parado por rebalance."),
    ("d7", "pedidos-api com memória alta e OOMKilled", "Vazamento de cache em memória."),
    ("d8", "pedidos-api com erro de migração de schema", "Migração travou com lock na tabela."),
    ("d9", "pedidos-api com cache Redis indisponível", "Redis reiniciou; leituras sem cache."),
    ("d10", "pedidos-api devolvendo dados velhos", "Réplica de leitura com atraso de 10 min."),
)


@dataclass(frozen=True)
class Probe:
    name: str
    text: str  # o incidente novo, como chegaria à análise
    expected: str  # chave do episódio certo
    paraphrase: bool  # sem palavras em comum suficientes para a busca textual


PROBES = (
    Probe(
        "pool, com outras palavras",
        "Erros 5xx na pedidos-api: timeout aguardando conexão do pool às 03h, sem deploy",
        "pool",
        paraphrase=True,
    ),
    Probe(
        "disco, com outras palavras",
        "Erros 5xx na pedidos-api: No space left on device ao escrever em /var/log",
        "disco",
        paraphrase=True,
    ),
    Probe(
        "tls, com outras palavras",
        "Erros 5xx na pedidos-api: handshake falhou, certificate has expired na integração",
        "tls",
        paraphrase=True,
    ),
    Probe(
        "desconto, termo exato",
        "Erros 5xx na pedidos-api: KeyError valor_desconto em GET /pedidos",
        "desconto",
        paraphrase=False,
    ),
    Probe(
        "rds, termo exato",
        "Erros 5xx na pedidos-api: não conecta no grolla-lab-db",
        "rds",
        paraphrase=False,
    ),
)


def episode(index: int, key: str, title: str, body: str) -> Memory:
    at = T0 - timedelta(days=60) + timedelta(days=index)  # os primeiros são os mais antigos
    return Memory(
        id=uuid5(NAMESPACE, key),
        kind=MemoryKind.EPISODE,
        subject=SERVICE,
        subject_kind=SubjectKind.SERVICE,
        title=title,
        body=body,
        confidence=Confidence.LEARNED,
        source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, None),
        created_at=at,
        updated_at=at,
        incident_id=uuid5(NAMESPACE, f"incidente-{key}"),
    )


async def position(memory: MemoryStore, probe: Probe, query: Query | None) -> int | None:
    """Posição (1-based) do episódio certo entre os parecidos; None se ficou de fora."""
    context = await memory.context_for([SERVICE], ["Erros 5xx na pedidos-api"], probe.text, query)
    ids = [e.id for e in context.episodes]
    wanted = uuid5(NAMESPACE, probe.expected)
    return ids.index(wanted) + 1 if wanted in ids else None


async def evaluate(
    memory: MemoryStore, embedder: TextEmbedder
) -> list[tuple[Probe, int | None, int | None]]:
    await memory.save([(episode(i, *row), None) for i, row in enumerate(EPISODES)])
    while await index_memory(
        memory=memory, embedder=embedder, clock=FakeClock(T0), policy=RagPolicy()
    ):
        pass
    results: list[tuple[Probe, int | None, int | None]] = []
    for probe in PROBES:
        textual = await position(memory, probe, None)
        hybrid = await position(memory, probe, await query_for(embedder, probe.text))
        results.append((probe, textual, hybrid))
    return results


def failures(results: list[tuple[Probe, int | None, int | None]]) -> list[str]:
    def top(rank: int | None) -> bool:
        return rank is not None and rank <= TOP

    found: list[str] = []
    for probe, textual, hybrid in results:
        if probe.paraphrase and not top(hybrid):
            found.append(f"{probe.name}: híbrida fora do top {TOP}")
        if top(textual) and not top(hybrid):
            found.append(f"{probe.name}: a textual acertava e a híbrida piorou")
    return found


def _rank(rank: int | None) -> str:
    return "fora" if rank is None else f"#{rank}"


async def main_async(size: RagSize, cache: Path) -> int:
    embedder = FastembedTextEmbedder(size, cache)
    with PostgresContainer(POSTGRES_IMAGE, driver=None) as postgres:
        url = postgres.get_connection_url()
        migrate(url)
        database = PostgresDatabase(url)
        try:
            results = await evaluate(PostgresMemoryStore(database), embedder)
        finally:
            await database.dispose()
    print(f"modelo {embedder.model} · {len(EPISODES)} episódios · top {TOP}")  # noqa: T201
    print(f"{'consulta':32} {'textual':>8} {'híbrida':>8}")  # noqa: T201
    for probe, textual, hybrid in results:
        print(f"{probe.name:32} {_rank(textual):>8} {_rank(hybrid):>8}")  # noqa: T201
    problems = failures(results)
    for problem in problems:
        print(f"FALHOU  {problem}")  # noqa: T201
    print("aceite: ok" if not problems else "aceite: FALHOU")  # noqa: T201
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval de recuperação da memória com RAG.")
    parser.add_argument("--size", choices=("small", "large"), default="small")
    arguments = parser.parse_args()
    cache = Path(os.environ.get("GROLLA_RAG_CACHE", str(DEFAULT_CACHE_DIR))).expanduser()
    sys.exit(asyncio.run(main_async(arguments.size, cache)))


if __name__ == "__main__":
    main()
