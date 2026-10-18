# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first so code changes do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY examples ./examples
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --home /home/devicelab --create-home devicelab \
    && mkdir -p /data/artifacts /state && chown -R devicelab /data /state
USER devicelab
ENV PATH="/app/.venv/bin:$PATH" \
    DEVICELAB_ARTIFACT_DIR=/data/artifacts

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["devicelab", "serve", "--host", "0.0.0.0", "--port", "8000"]
