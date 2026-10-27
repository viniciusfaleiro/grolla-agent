"""Suíte de contrato de TextEmbedder.

O adapter sobrescreve `embedder`. Regras que a suíte fixa: o modelo tem nome; um vetor por texto,
na mesma ordem, todos com a mesma dimensão (consulta e trecho) e normalizados; o mesmo texto dá
o mesmo vetor; lista vazia devolve [].
"""

import math

import pytest
from grolla.application.memory_store import TextEmbedder

TEXTS = [
    "timeout aguardando conexão do pool",
    "esgotamento de conexões do banco pela rotina de reconciliação",
    "## Pool de conexões esgotado\nReinicie as conexões presas.",
]
TOLERANCE = 1e-3


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))


class TextEmbedderContract:
    @pytest.fixture
    def embedder(self) -> TextEmbedder:
        raise NotImplementedError

    async def test_model_has_a_name(self, embedder: TextEmbedder) -> None:
        assert embedder.model.strip()

    async def test_one_normalized_vector_per_text_with_a_constant_dimension(
        self, embedder: TextEmbedder
    ) -> None:
        passages = await embedder.embed(TEXTS, "passage")
        [query] = await embedder.embed([TEXTS[0]], "query")

        assert len(passages) == len(TEXTS)
        assert {len(v) for v in [*passages, query]} == {len(query)}
        assert len(query) > 1
        assert all(abs(_norm(v) - 1) < TOLERANCE for v in [*passages, query])

    async def test_vectors_keep_the_order_of_the_texts(self, embedder: TextEmbedder) -> None:
        forward = await embedder.embed(TEXTS, "passage")
        backward = await embedder.embed(list(reversed(TEXTS)), "passage")
        alone = await embedder.embed([TEXTS[1]], "passage")

        for a, b in zip(forward, reversed(backward), strict=True):
            assert a == pytest.approx(b, abs=TOLERANCE)
        assert alone[0] == pytest.approx(forward[1], abs=TOLERANCE)

    async def test_empty_list_gives_empty_list(self, embedder: TextEmbedder) -> None:
        assert await embedder.embed([], "passage") == []
