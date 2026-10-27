"""Embeddings locais com fastembed (ONNX Runtime): o modelo roda no worker, sem rede.

Os modelos vêm embutidos na imagem do worker (`GROLLA_RAG_MODELS` no build) e são carregados só
dos arquivos locais, no construtor: modelo ausente levanta EmbeddingError na subida do worker.

O e5 foi treinado com os prefixos "query: " e "passage: " e o MiniLM paraphrase não usa
prefixo (segundo a descrição dos modelos no fastembed). Os vetores saem normalizados: o pooling
desses dois modelos no fastembed não normaliza.
"""

import asyncio
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from fastembed import TextEmbedding
from fastembed.common.types import NumpyArray

from grolla.application.memory_store import EmbeddingError, EmbeddingKind

RagSize = Literal["small", "large"]

# Tamanho no YAML → modelo multilíngue do fastembed.
MODELS: dict[RagSize, str] = {
    "small": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # 384 dim, 0,22 GB
    "large": "intfloat/multilingual-e5-large",  # 1024 dim, 2,2 GB
}
DEFAULT_CACHE_DIR = Path("/opt/grolla/models")
PREFIXED = {MODELS["large"]}  # modelos treinados com "query: " / "passage: "
BATCH_SIZE = 32


class _Model(Protocol):
    def embed(self, documents: list[str], batch_size: int) -> Iterable[NumpyArray]: ...


def _load(model: str, cache_dir: Path) -> _Model:
    """Só arquivos locais: o worker nunca baixa modelo."""
    return TextEmbedding(model, cache_dir=str(cache_dir), local_files_only=True)


class FastembedTextEmbedder:
    def __init__(self, size: RagSize, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self._name = MODELS[size]
        try:
            self._model = _load(self._name, cache_dir)
        except Exception as error:  # o fastembed levanta ValueError sem o modelo; onnx, outras
            raise EmbeddingError(
                f"modelo de embeddings {self._name} ausente ou inválido em {cache_dir}: "
                f"embuta-o na imagem do worker (build arg GROLLA_RAG_MODELS) ({error})"
            ) from error

    @property
    def model(self) -> str:
        return self._name

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._embed, prefixed(self._name, texts, kind))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            return [normalized(vector) for vector in self._model.embed(texts, BATCH_SIZE)]
        except Exception as error:  # falha do ONNX Runtime, de qualquer tipo
            raise EmbeddingError(f"{self._name} falhou: {type(error).__name__}") from error


def prefixed(model: str, texts: Sequence[str], kind: EmbeddingKind) -> list[str]:
    """Os textos com o prefixo que o modelo pede ("query: " ou "passage: "), ou como vieram."""
    if model not in PREFIXED:
        return list(texts)
    return [f"{kind}: {text}" for text in texts]


def normalized(vector: NumpyArray) -> list[float]:
    norm = float(np.linalg.norm(vector))
    values = vector / norm if norm > 0 else vector
    return [float(value) for value in values]
