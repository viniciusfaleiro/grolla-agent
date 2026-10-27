# Imagem do grolla-worker. Build a partir da raiz do repositório:
#   docker build -f packages/grolla-worker/Dockerfile .
# Só o grolla-worker e as dependências dele entram na imagem final.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
# O venv é copiado para o mesmo caminho na imagem final, para os shebangs continuarem válidos.
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# Dependências primeiro, para o cache de camadas sobreviver a mudanças de código.
COPY pyproject.toml uv.lock ./
COPY packages/grolla-core/pyproject.toml packages/grolla-core/
COPY packages/grolla-api/pyproject.toml packages/grolla-api/
COPY packages/grolla-worker/pyproject.toml packages/grolla-worker/
COPY e2e/fakes/pyproject.toml e2e/fakes/
RUN uv sync --frozen --no-dev --package grolla-worker --no-install-workspace

COPY packages/grolla-core/src packages/grolla-core/src
COPY packages/grolla-worker/src packages/grolla-worker/src
RUN uv sync --frozen --no-dev --package grolla-worker --no-editable

# Modelos de embeddings da memória com RAG, embutidos na imagem: em produção nada é baixado.
# GROLLA_RAG_MODELS escolhe os tamanhos ("small", "large" ou "small large").
FROM build AS models
ARG GROLLA_RAG_MODELS="small large"
RUN /app/.venv/bin/python -c "import sys; from fastembed import TextEmbedding; \
from grolla.adapters.fastembed import MODELS; \
[TextEmbedding(MODELS[s], cache_dir='/opt/grolla/models') for s in sys.argv[1:]]" $GROLLA_RAG_MODELS

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 grolla
COPY --from=build /app/.venv /app/.venv
COPY --from=models /opt/grolla/models /opt/grolla/models
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER grolla
CMD ["grolla-worker"]
