"""FastembedTextEmbedder com o modelo `small` real, lido do cache em GROLLA_RAG_CACHE.

Pula sem o modelo no cache: o teste não baixa nada. Para rodar, baixe uma vez (0,22 GB):

    uv run python -c "from fastembed import TextEmbedding; TextEmbedding(
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
        cache_dir='$HOME/.cache/grolla-models')"
    GROLLA_RAG_CACHE=~/.cache/grolla-models make check
"""

import os
from functools import cache
from pathlib import Path

import pytest
from grolla.adapters.fastembed import FastembedTextEmbedder
from grolla.application.memory_store import EmbeddingError

pytestmark = pytest.mark.integration


@cache
def small_model() -> FastembedTextEmbedder | None:
    """Carregado uma vez por sessão; None sem o modelo no cache."""
    cache_dir = os.environ.get("GROLLA_RAG_CACHE")
    if not cache_dir:
        return None
    try:
        return FastembedTextEmbedder("small", Path(cache_dir).expanduser())
    except EmbeddingError:
        return None


@pytest.fixture
def embedder() -> FastembedTextEmbedder:
    model = small_model()
    if model is None:
        pytest.skip("modelo small fora do cache (defina GROLLA_RAG_CACHE)")
    return model


def similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def test_vectors_are_normalized_with_the_model_dimension(
    embedder: FastembedTextEmbedder,
) -> None:
    vectors = await embedder.embed(["pool de conexões", "deploy"], "passage")

    assert [len(v) for v in vectors] == [384, 384]
    assert [similarity(v, v) for v in vectors] == pytest.approx([1, 1], abs=1e-5)


async def test_paraphrase_in_portuguese_is_closer_than_an_unrelated_error(
    embedder: FastembedTextEmbedder,
) -> None:
    [query] = await embedder.embed(
        ["tempo esgotado esperando uma conexão livre com o banco de dados"], "query"
    )
    same, other = await embedder.embed(
        [
            "o pool de conexões com o PostgreSQL ficou sem conexões livres",
            "KeyError após deploy",
        ],
        "passage",
    )

    assert similarity(query, same) > similarity(query, other)


async def test_pool_timeout_is_closer_to_connection_exhaustion(
    embedder: FastembedTextEmbedder,
) -> None:
    [query] = await embedder.embed(["timeout aguardando conexão do pool"], "query")
    exhaustion, key_error = await embedder.embed(
        ["esgotamento de conexões do banco pela rotina de reconciliação", "KeyError após deploy"],
        "passage",
    )

    assert similarity(query, exhaustion) > similarity(query, key_error)
