# ADR-0001: Technology stack

**Status:** Accepted

## Context

The platform analyses Python repositories first (Django, FastAPI, Flask). It needs an HTTP API,
long-running background jobs (analysis, test execution, mutation testing), durable storage for
runs and results, and it must be easy to run locally and in CI.

## Decision

- **Python 3.11+**. It is the language of the first target ecosystem, and the analysis tools we
  build on (`ast`, pytest, coverage.py, mutation tools) are Python-native.
- **FastAPI + Pydantic v2** for the API: typed contracts, OpenAPI for free, async I/O.
- **PostgreSQL + SQLAlchemy 2 (async, asyncpg) + Alembic.** Relational data (projects → runs →
  results → findings), row-level locking for job state, JSONB available for tool outputs later.
- **Celery + Redis** for background jobs. Mature, supports late acknowledgement, time limits and
  routing to dedicated queues, which later phases need for isolated test-runner workers.
- **uv** for dependency management with a lock file. **ruff**, **mypy --strict**, **pytest**.
- **structlog** for structured JSON logs shared by API and worker.

## Consequences

- One language across API, worker and analyzers, so there is no serialisation boundary between
  analysis code and the platform.
- Celery tasks are synchronous. Async services are called with `asyncio.run` per task, using an
  unpooled engine (see [architecture](../architecture.md)).
- The frontend (Phase 12) is a separate app (React + Vite + TypeScript + Tailwind) that talks
  to this API. It is not part of the Python package.
