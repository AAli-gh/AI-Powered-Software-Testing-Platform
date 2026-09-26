# Testing

A platform about software testing needs a trustworthy test suite of its own. The rules:

1. **No mocks for core infrastructure.** Database code is tested against real PostgreSQL, the
   queue against real Redis with a real (in-process) Celery worker, and migrations against the
   real schema.
2. **Test doubles only at a boundary, and only where a real one adds nothing.** API tests use
   a recording `TaskDispatcher` (`tests/doubles.py`) so they can assert *what* was enqueued
   without a worker. The real Celery dispatcher is covered by `tests/integration/test_worker.py`.
   The LLM provider is the other boundary: `ScriptedLLM` returns fixed replies because no API
   key is available in CI, while everything behind it (static checks, the real Docker
   sandbox, the fix loop and persistence) runs for real.
3. **Every bug fix gets a regression test** that fails without the fix.
4. **Coverage is a floor, not a goal.** CI enforces ≥ 85 % (line + branch). The suite is at
   100 % in CI. Locally, as root, a few permission tests are skipped because root bypasses file
   permissions.
5. **Dogfooding.** The analyzers are also run on TestMind itself. That found false positives
   (a string mentioning `DJANGO_SETTINGS_MODULE`, `pathlib`'s `/` treated as division, and
   `# noqa` side-effect imports) and real bugs (nested functions counted in complexity,
   line numbers shifted by `\x0c`). Each is now a regression test.
6. **Cross-check against the reference tool.** Coverage computed by TestMind is compared with
   coverage.py's own totals and function regions on every example repository
   (`tests/sandbox/test_coverage.py`). The one intended difference is asserted explicitly.

## Suites

| Suite | Path | Needs | What it covers |
|---|---|---|---|
| Unit | `tests/unit/` | nothing | Settings validation, logging, run state machine, path confinement (traversal, symlinks, permissions), Celery configuration, error mapping |
| Analyzer | `tests/unit/analyzers/` | nothing | Every detector in isolation (hostile and malformed inputs included), plus **golden tests** against the real repositories in `examples/` (Django, FastAPI, Flask, plain pytest) |
| Code analyzer | `tests/unit/analyzers/code/` | nothing | Complexity, symbols, and every rule with positive **and** negative cases. A golden test requires exactly the defects documented in `examples/buggy_inventory/EXPECTED_FINDINGS.md` |
| Integration | `tests/integration/` | PostgreSQL + Redis | Repositories and services, row-lock races, migrations (drift + reversibility), run executor, full queue path with a real worker |
| Coverage engine | `tests/unit/coverage_engine/` | nothing | Parsing coverage.py JSON (hostile paths, invalid reports), file/function mapping, nested scopes, missing-line ranges, gap ranking, comparisons |
| Failure engine | `tests/unit/failure_engine/` | nothing | Plugin-record parsing (hostile input), categories, grouping, linking and evidence on a **golden capture** of `examples/failing_suite`, the AI step's checks (evidence verification, confidence, repair, limits) with a scripted model |
| Regression | `tests/unit/regression/`, `tests/unit/test_vcs.py` | nothing | Git access on real repositories built from objects (`tests/gitrepo.py`): revisions, changes, safe materialisation, refused layouts; AST diff (comments, formatting and docstrings are not changes); selection rules; comparison |
| Sandbox | `tests/sandbox/` | Docker + `make sandbox-image` (+ PostgreSQL/Redis for the end-to-end tests) + network for dependency wheels | The real sandbox's isolation (no network, read-only, non-root, no host env, timeout kill), the full AI test-generation loop with a scripted model, runner-image builds, the example projects' own suites with coverage (agreement with coverage.py), the coverage-guided loop (baseline → generation → measured improvement), the failure-capture plugin against the golden fixture, failure analysis end to end, the regression engine on a real git history (both modes) and through the real worker, and a no-doubles run through Celery, the worker and the sandbox |
| API | `tests/api/` | PostgreSQL (+ Redis for `/ready`) | Every endpoint over HTTP: status codes, error bodies, pagination, request IDs, readiness with dependencies down |
| Smoke | `scripts/smoke_test.sh` | running stack | Containers wired together: readiness, create project, traversal rejected, run completes, cleanup |

Tests under `tests/integration/` and `tests/api/` are automatically marked `integration`.

## Running

```bash
make test-services     # PostgreSQL on 127.0.0.1:55432, Redis on 127.0.0.1:56379 (tmpfs, disposable)
make sandbox-image     # the Docker image the sandbox tests run code in
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
