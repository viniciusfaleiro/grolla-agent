"""FastembedTextEmbedder sem o modelo real: prefixos, normalização, ordem e modelo ausente."""

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pytest
from fastembed.common.types import NumpyArray
from grolla.adapters import fastembed as adapter
from grolla.adapters.fastembed import MODELS, FastembedTextEmbedder, normalized, prefixed
from grolla.application.memory_store import EmbeddingError


class FakeModel:
    """Um vetor [tamanho do texto, 0, 0, 1] por texto; grava o que recebeu."""

    def __init__(self, error: Exception | None = None) -> None:
        self.received: list[list[str]] = []
        self.error = error

    def embed(self, documents: list[str], batch_size: int) -> Iterable[NumpyArray]:
        del batch_size
        if self.error:
            raise self.error
        self.received.append(documents)
        return [np.array([len(text), 0.0, 0.0, 1.0]) for text in documents]


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeModel) -> FakeModel:
    def load(model: str, cache_dir: Path) -> FakeModel:
        del model, cache_dir
        return fake

    monkeypatch.setattr(adapter, "_load", load)
    return fake


def test_e5_asks_for_query_and_passage_prefixes() -> None:
    assert prefixed(MODELS["large"], ["pool"], "query") == ["query: pool"]
    assert prefixed(MODELS["large"], ["pool"], "passage") == ["passage: pool"]


def test_minilm_paraphrase_takes_the_texts_as_they_are() -> None:
    assert prefixed(MODELS["small"], ["pool", "banco"], "query") == ["pool", "banco"]


def test_vectors_are_normalized_and_zero_stays_zero() -> None:
    assert normalized(np.array([3.0, 4.0])) == pytest.approx([0.6, 0.8])
    assert normalized(np.array([0.0, 0.0])) == [0.0, 0.0]


async def test_embed_keeps_the_order_and_normalizes(monkeypatch: pytest.MonkeyPatch) -> None:
    model = use(monkeypatch, FakeModel())
    embedder = FastembedTextEmbedder("large", Path("/modelos"))

    vectors = await embedder.embed(["a", "abc"], "passage")

    assert model.received == [["passage: a", "passage: abc"]]
    assert [v[0] / v[3] for v in vectors] == pytest.approx([10, 12])  # "passage: a", "…abc"
    assert [sum(x * x for x in v) for v in vectors] == pytest.approx([1, 1])
    assert embedder.model == MODELS["large"]


async def test_empty_list_does_not_touch_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    model = use(monkeypatch, FakeModel())

    assert await FastembedTextEmbedder("small").embed([], "query") == []
    assert model.received == []


async def test_model_failure_becomes_embedding_error(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeModel(RuntimeError("onnxruntime: falha")))

    with pytest.raises(EmbeddingError, match="falhou: RuntimeError"):
        await FastembedTextEmbedder("small").embed(["pool"], "query")


def test_missing_model_fails_on_construction_naming_it(tmp_path: Path) -> None:
    with pytest.raises(EmbeddingError) as error:
        FastembedTextEmbedder("small", tmp_path)

    assert MODELS["small"] in str(error.value)
    assert "GROLLA_RAG_MODELS" in str(error.value)
