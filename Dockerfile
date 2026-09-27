# syntax=docker/dockerfile:1.7
# One image definition, two runtime targets:
#   docker build --target api    -t testmind-api .
#   docker build --target worker -t testmind-worker .

ARG PYTHON_VERSION=3.12

# ------------------------------------------------------------------ builder
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN pip install "uv==0.8.17"

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

# ------------------------------------------------------------------ runtime base
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="TestMind" \
      org.opencontainers.image.description="AI-powered software testing platform" \
      org.opencontainers.image.licenses="MIT"

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESTMIND_REPOS_ROOT=/repos

RUN groupadd --system --gid 10001 testmind \
 && useradd --system --uid 10001 --gid testmind --no-create-home --shell /usr/sbin/nologin testmind \
 && mkdir -p /repos /imports && chown testmind:testmind /repos /imports

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations

USER testmind

# ------------------------------------------------------------------ api
FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status != 200)"]
CMD ["uvicorn", "--factory", "testmind.api.app:create_app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

# ------------------------------------------------------------------ worker
FROM runtime AS worker
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["sh", "-c", "celery -A testmind.workers.main inspect ping -d celery@$HOSTNAME --timeout 5"]
CMD ["celery", "-A", "testmind.workers.main", "worker", \
     "--loglevel=INFO", "--concurrency=2", "--without-gossip", "--without-mingle"]
