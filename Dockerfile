# syntax=docker/dockerfile:1.7
# One image definition, three runtime targets:
#   docker build --target api           -t testmind-api .
#   docker build --target worker        -t testmind-worker .
#   docker build --target sandbox-proxy -t testmind-sandbox-proxy .

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

# ------------------------------------------------------------------ docker CLI
# Only the client binary, pinned by version and checksum. The worker uses it to reach the
# sandbox proxy (DOCKER_HOST); it never gets the Docker socket itself.
FROM python:${PYTHON_VERSION}-slim AS docker-cli
ARG TARGETARCH
ARG DOCKER_CLI_VERSION=29.8.1
ARG DOCKER_CLI_SHA256_AMD64=d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70
ARG DOCKER_CLI_SHA256_ARM64=667395fbffab52901b80181dfbb39ea76da2fbd7642c4fbddd24e42146b07b48
RUN python - <<'PY'
import hashlib, io, os, tarfile, urllib.request
arch = {"amd64": ("x86_64", os.environ["DOCKER_CLI_SHA256_AMD64"]),
        "arm64": ("aarch64", os.environ["DOCKER_CLI_SHA256_ARM64"])}[os.environ.get("TARGETARCH") or "amd64"]
url = f"https://download.docker.com/linux/static/stable/{arch[0]}/docker-{os.environ['DOCKER_CLI_VERSION']}.tgz"
data = urllib.request.urlopen(url, timeout=120).read()
digest = hashlib.sha256(data).hexdigest()
if digest != arch[1]:
    raise SystemExit(f"checksum mismatch for {url}: {digest}")
with tarfile.open(fileobj=io.BytesIO(data)) as tar:
    member = tar.getmember("docker/docker")
    with open("/usr/local/bin/docker", "wb") as out:
        out.write(tar.extractfile(member).read())
os.chmod("/usr/local/bin/docker", 0o755)
PY

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
# The Docker CLI, pointed at the sandbox proxy by docker-compose.sandbox.yml. The classic
# builder is used for runner images: BuildKit's sessions cannot be checked by the proxy.
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
ENV DOCKER_BUILDKIT=0
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["sh", "-c", "celery -A testmind.workers.main inspect ping -d celery@$HOSTNAME --timeout 5"]
CMD ["celery", "-A", "testmind.workers.main", "worker", \
     "--loglevel=INFO", "--concurrency=2", "--without-gossip", "--without-mingle"]

# ------------------------------------------------------------------ sandbox proxy
# The only container with the Docker socket (docker-compose.sandbox.yml). It runs as root
# only so it can open the socket (owned by root); every capability is dropped.
FROM runtime AS sandbox-proxy
USER root
EXPOSE 2375
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 2375), 2).close()"]
CMD ["python", "-m", "testmind.sandbox_proxy"]
