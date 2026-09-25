# ADR-0004: Test against real PostgreSQL and Redis instead of mocks

**Status:** Accepted

## Context

Mocked sessions and brokers pass tests that production would fail: row locks, constraint
violations, `RETURNING` behaviour, transaction visibility between connections, and Celery
serialisation and task registration. Two real bugs were caught during Phase 1 precisely because
the tests ran against real services:

1. `updated_at` (server-side `onupdate`) was expired after an `UPDATE` and triggered implicit I/O
   under asyncio. Fixed with `eager_defaults=True`.
2. Celery's default `shared=True` task registration attached the first app's task (with its
   settings) to every later app instance. Fixed with `shared=False`.

## Decision

- Integration and API tests use real PostgreSQL and Redis: `docker-compose.test.yml` locally,
  GitHub Actions service containers in CI.
- The schema is created by the real Alembic migrations.
- The only test double is at the queue boundary (`RecordingDispatcher`), used where a running
  worker adds nothing. The real Celery path has its own end-to-end tests.

## Consequences

- Running the full suite needs Docker (`make test-services`). Unit tests alone do not.
- The suite takes seconds rather than milliseconds. That is acceptable at this size, and tests
  truncate tables instead of re-creating the schema.
