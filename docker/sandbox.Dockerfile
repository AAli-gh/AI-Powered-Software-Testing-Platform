# Image in which untrusted code (a repository and its generated tests) is executed.
# It contains only Python, pytest and coverage.py; it holds no TestMind code and no secrets.
#   docker build -f docker/sandbox.Dockerfile -t testmind-sandbox:local .
FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install "pytest==8.4.2" "coverage==7.16.1" "pytest-cov==7.1.0"
COPY docker/coveragerc /opt/testmind/coveragerc
COPY docker/coveragerc_contexts /opt/testmind/coveragerc_contexts
COPY docker/testmind_failures.py /opt/testmind/testmind_failures.py
COPY docker/testmind_api.py /opt/testmind/testmind_api.py
COPY docker/testmind_mutate.py /opt/testmind/testmind_mutate.py

# Runs as nobody; everything the container may write is a tmpfs or the /out bind mount.
USER 65534:65534
WORKDIR /workspace
