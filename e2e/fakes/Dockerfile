# Imagem do grolla-fakes. Build a partir da raiz do repositório:
#   docker build -f e2e/fakes/Dockerfile .
# Só o grolla-fakes e as dependências dele entram na imagem final.
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
RUN uv sync --frozen --no-dev --package grolla-fakes --no-install-workspace

COPY e2e/fakes/src e2e/fakes/src
RUN uv sync --frozen --no-dev --package grolla-fakes --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --system --uid 10001 grolla
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER grolla
CMD ["grolla-fakes"]
