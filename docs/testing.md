# Testing

A platform about software testing needs a trustworthy test suite of its own. The rules:

1. **No mocks for core infrastructure.** Database code is tested against real PostgreSQL, the
   queue against real Redis with a real (in-process) Celery worker, and migrations against the
   real schema.
2. **Test doubles only at a boundary, and only where a real one adds nothing.** API tests use
   a recording `TaskDispatcher` (`tests/doubles.py`) so they can assert *what* was enqueued
   without a worker. The real Celery dispatcher is covered by `tests/integration/test_worker.py`.
3. **Every bug fix gets a regression test** that fails without the fix.
4. **Coverage is a floor, not a goal.** CI enforces ≥ 85 % (line + branch). Phase 1 is at 100 %.

## Suites

| Suite | Path | Needs | What it covers |
|---|---|---|---|
| Unit | `tests/unit/` | nothing | Settings validation, logging, run state machine, path confinement (traversal, symlinks, permissions), Celery configuration, error mapping |
| Integration | `tests/integration/` | PostgreSQL + Redis | Repositories and services, row-lock races, migrations (drift + reversibility), run executor, full queue path with a real worker |
| API | `tests/api/` | PostgreSQL (+ Redis for `/ready`) | Every endpoint over HTTP: status codes, error bodies, pagination, request IDs, readiness with dependencies down |
| Smoke | `scripts/smoke_test.sh` | running stack | Containers wired together: readiness, create project, traversal rejected, run completes, cleanup |

Tests under `tests/integration/` and `tests/api/` are automatically marked `integration`.

## Running

```bash
make test-services     # PostgreSQL on 127.0.0.1:55432, Redis on 127.0.0.1:56379 (tmpfs, disposable)
make test              # everything, with coverage
make test-unit         # no services needed
uv run pytest tests/api/test_runs_api.py -k cancel   # anything pytest accepts

make up && make smoke  # end-to-end against the Docker stack
```

Override the service URLs with `TESTMIND_TEST_DATABASE_URL` and `TESTMIND_TEST_REDIS_URL`
(CI does this to use its service containers).

## How the fixtures work

- `migrated_database` (session): runs `alembic downgrade base` and then `upgrade head` once, so
  the schema always comes from the real migrations.
- `engine` (per test): unpooled engine, `TRUNCATE` of all tables. Tests are isolated and
  order-independent.
- `repos_root` / `settings` (per test): a temporary repositories root with one valid project
  directory (`sample/`).
- `app` / `client` (API tests): the real app factory with its lifespan, driven through
  `httpx.ASGITransport`.
- `worker` (queue tests): `celery.contrib.testing.worker.start_worker` running the real
  task against the real broker.

`filterwarnings = error` is set, so any warning (for example an un-awaited coroutine) fails the
test run.

## Coverage notes

`[tool.coverage.run] concurrency = ["thread", "greenlet"]` is required. SQLAlchemy's asyncio
layer performs I/O through greenlets, and without it coverage.py misses every line that runs
after a database call.
