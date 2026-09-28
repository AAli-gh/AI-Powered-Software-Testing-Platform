# Testing

A platform about software testing needs a trustworthy test suite of its own. The rules:

1. **No mocks for core infrastructure.** Database code is tested against real PostgreSQL, the
   queue against real Redis with a real (in-process) Celery worker, and migrations against the
   real schema.
2. **Test doubles only at a boundary, and only where a real one adds nothing.** API tests use
   a recording `TaskDispatcher` (`tests/doubles.py`) so they can assert *what* was enqueued
   without a worker. The real Celery dispatcher is covered by `tests/integration/test_worker.py`.
   The LLM provider is another boundary: `ScriptedLLM` returns fixed replies because no API
   key is available in CI, while everything behind it (static checks, the real Docker
   sandbox, the fix loop and persistence) runs for real. GitHub is the third:
   `tests/github_fake.py` is a local fake of the REST API. It verifies the App JWT against
   the public key, issues installation tokens, answers only for the installation's
   repositories, and serves real tarballs through a redirect. It runs in process
   (`httpx.ASGITransport`) or on a real port with uvicorn for the worker tests. TestMind's
   client, webhooks, extraction, analysis and persistence all run for real against it.
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
| Mutation engine | `tests/unit/mutation_engine/` | nothing | Every operator with positive and negative cases (docstrings, annotations, decorators, f-strings, string arithmetic and non-compiling mutants are skipped; duplicates are generated once), planning from per-test coverage, timeouts, scores, every driver failure mode as `error` with a note, and the suggester's acceptance rules with a scripted model |
| API engine | `tests/unit/api_engine/` | nothing | Endpoint discovery for every supported construct and every unresolved one (notes), path canonicalisation, golden endpoints of the example applications, API coverage (merge, cross-check, attribution, gaps), the capture parser (hostile input), probe planning, the generated module and every verdict, the AI API test loop with a scripted model |
| Sandbox | `tests/sandbox/` | Docker + `make sandbox-image` (+ PostgreSQL/Redis for the end-to-end tests) + network for dependency wheels | The real sandbox's isolation (no network, read-only, non-root, no host env, timeout kill), the full AI test-generation loop with a scripted model, runner-image builds, the example projects' own suites with coverage (agreement with coverage.py), the coverage-guided loop (baseline → generation → measured improvement), the failure-capture plugin against the golden fixture, failure analysis end to end, the regression engine on a real git history (both modes) and through the real worker, the mutation driver (verdicts, per-mutant timeout kill, source mismatch, extra files), mutation testing of `examples/shipping_rates` against its documented result, the AI suggestion checks in the real sandbox, the API capture plugin (examples' API coverage equal to static discovery, FastAPI mounts, exceptions and wrong methods), `examples/buggy_api` against its documented results with probes, no false alarms on the well-built examples, AI API test checks, probes through the real worker, Playwright tests in the browser image (skipped without it, required in CI), a signed webhook through Celery and the real sandbox with the fake GitHub on a real port (the pull request fixture's regressions, new finding and untested changes), a GitHub import followed by its analysis run, and a no-doubles run through Celery, the worker and the sandbox |
| GitHub | `tests/unit/github/`, `tests/unit/test_archive.py`, `tests/unit/test_snapshots.py`, `tests/integration/test_github_integration.py` | nothing / PostgreSQL | App JWT and token caching, every client call and error path against the fake, webhook signatures and events, hostile archives (links, absolute and `..` paths, duplicates, limits), snapshots, new-finding matching, conclusions, suggestions and markdown; the service and both executors with the real database (de-duplication, owners, check run and comment updates, failures reported as `neutral`, idempotent issues, imports) |
| API | `tests/api/` | PostgreSQL (+ Redis for `/ready`) | Every endpoint over HTTP: status codes, error bodies, pagination, request IDs, readiness with dependencies down |
| Dashboard | `web/src/**/*.test.ts(x)` (Vitest), `web/e2e/` (Playwright) | Node (+ a running stack for Playwright) | Every page through the application's own routes with the API replaced at `fetch` by answers typed against the OpenAPI schema, page fixtures captured from real runs of the examples, charts (ticks, gaps, keyboard, tooltips), forms and request bodies, errors; and in Chromium through the real stack: register, analyse, run tests, follow the results. See [dashboard.md](dashboard.md#tests) |
| Smoke | `scripts/smoke_test.sh` | running stack | Containers wired together: readiness, create project, traversal rejected, run completes, code analysis, AI generation (`503` without a key), test execution, coverage and failures, mutation testing, API endpoint discovery and API coverage with probes, regression (`409` for a non-git example), the GitHub endpoints (`503` without an App, `401` for an unsigned delivery with one), cleanup |

Tests under `tests/integration/` and `tests/api/` are automatically marked `integration`.

## Running

```bash
make test-services     # PostgreSQL on 127.0.0.1:55432, Redis on 127.0.0.1:56379 (tmpfs, disposable)
make sandbox-image     # the Docker image the sandbox tests run code in
make test              # everything, with coverage
make test-unit         # no services needed
uv run pytest tests/api/test_runs_api.py -k cancel   # anything pytest accepts

make up && make smoke  # end-to-end against the Docker stack
make web-check         # dashboard: lint, format, types, component tests with coverage, build
TESTMIND_WEB_URL=http://127.0.0.1:8080 make web-e2e   # dashboard in a real browser
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
