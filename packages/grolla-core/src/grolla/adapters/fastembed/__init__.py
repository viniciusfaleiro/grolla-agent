"""Embeddings locais com fastembed (ONNX Runtime): o modelo roda no worker, sem rede.

Os modelos vêm embutidos na imagem do worker (`GROLLA_RAG_MODELS` no build) e são carregados só
dos arquivos locais. Contrato da memória com RAG: o corpo é do grolla-integracoes.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from grolla.application.memory_store import EmbeddingKind

RagSize = Literal["small", "large"]

# Tamanho no YAML → modelo multilíngue do fastembed.
MODELS: dict[RagSize, str] = {
    "small": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # 384 dim, 0,22 GB
    "large": "intfloat/multilingual-e5-large",  # 1024 dim, 2,2 GB
}
DEFAULT_CACHE_DIR = Path("/opt/grolla/models")


class FastembedTextEmbedder:
    def __init__(self, size: RagSize, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
        self._size: RagSize = size
        self._cache_dir = cache_dir

    @property
    def model(self) -> str:
        return MODELS[self._size]

    async def embed(self, texts: Sequence[str], kind: EmbeddingKind) -> list[list[float]]:
        raise NotImplementedError
