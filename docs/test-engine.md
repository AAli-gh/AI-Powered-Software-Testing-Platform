# Test engine

Phase 5 runs a repository's **own test suite**, optionally together with the test modules of
an AI test generation (Phase 4), inside the sandbox **with the project's dependencies
installed**. The result is a structured report: totals, duration, every test with its
file, line, outcome, message and traceback, the pytest exit code and what it means, and
the dependency installation log.

Code: `src/testmind/test_engine/` (runner, report models) and `src/testmind/sandbox/`
(`runner_image.py`, `docker_sandbox.py`).

## Flow

```text
POST /api/v1/runs/{run_id}/test-executions  {"generation_id": optional}
  → test_executions row (queued) → Celery task → worker
  → Runner image:  sandbox base image + project dependencies
                   (cached per hash of base image + requirement list)
  → Sandbox run:   pytest on the project's test files (+ generated modules),
                   no network, read-only repository, non-root, limits, timeout
  → TestRunReport stored (JSONB) → summary / per-test / logs endpoints
```

## Dependencies: wheels only

The runner image is built with:

```dockerfile
FROM testmind-sandbox:local
USER root
COPY requirements.txt /opt/testmind/requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /opt/testmind/requirements.txt
USER 65534:65534
```

- **Which dependencies:** the *runtime* and *development* dependencies found by the
  repository analyzer (Phase 2), from every supported manifest. *Optional* extras are not
  installed. Specifiers that are not PEP 440 (Poetry's `^1.2`) are dropped, and a note is
  added to the report.
- **Wheels only (`--only-binary=:all:`):** no third-party `setup.py` is ever executed while
  installing. A package without a wheel makes the installation fail with the pip log in the
  report (`install_failed`). TestMind does not fall back to building from source.
- The project itself is **not** installed. It is importable through `PYTHONPATH`
  (`/workspace/src` and `/workspace/src/src` for `src/` layouts).
- Images are tagged `testmind-runner:<hash>` and reused while the requirement list **and the
  base image's content id** are unchanged. Rebuilding the sandbox image (for example to
  upgrade pytest or coverage.py) therefore never reuses runner images built on the old one.

## Test selection and configuration

- The project's test files come from the repository analysis (the same classification used
  everywhere else). They are passed to pytest explicitly, and `conftest.py` files are still
  honoured.
- pytest runs from the repository root with `-p no:cacheprovider`, `-o addopts=` (the
  project's `addopts` may require plugins or write files) and `-o junit_family=xunit1` (so
  every test case has a file and line).
- **Coverage** is measured in the same run with coverage.py and TestMind's own config file;
  see [coverage.md](coverage.md).
- **Regression runs** additionally record per-test coverage contexts and can run only a
  selection of test files or pytest nodeids ([regression.md](regression.md)). Nodeids are
  validated: they must name one of the project's test files and contain no control characters.
- **API requests** the tests send to in-process applications are recorded by a second
  plugin, `testmind_api`, and turned into API coverage; `api_probes` adds TestMind's probe
  module ([api-testing.md](api-testing.md)).
- **Browser tests:** projects with Playwright tests get runner images built on the browser
  sandbox image (headless Chromium), and a 256 MB `/tmp` ([api-testing.md](api-testing.md#6-browser-tests-playwright)).
- **Mutation runs** use the baseline's per-test contexts to run each mutant against only the
  tests that executed its lines, in one container driven by `docker/testmind_mutate.py`
  ([mutation.md](mutation.md)).
- **Failures** are captured as structured data by TestMind's pytest plugin and analysed
  after every run ([failure-analysis.md](failure-analysis.md)). Suite runs use
  `--continue-on-collection-errors`, so a test module that fails to import is reported as its
  own error instead of stopping the whole run.
- **Django:** when the project sets `DJANGO_SETTINGS_MODULE` to a string literal (in
  `manage.py`, `wsgi.py`, …), the value is extracted from the AST during repository
  analysis and passed to the sandbox, so `pytest-django` finds the settings.

## Report

| Field | Meaning |
|---|---|
| `outcome` | `passed`, `failed`, `error` (collection/internal/usage error), `no_tests`, `timed_out`, `install_failed`, `sandbox_unavailable` |
| `exit_code` / `exit_meaning` | pytest's exit code and its documented meaning (0–5) |
| `totals` | total, passed, failed, errors, skipped, xfailed |
| `tests[]` | `nodeid` (`file::name`), file, line, outcome, duration, message, traceback, `generated` |
| `install` | image tag, requirements, from cache, notes, installation log |
| `environment` | variables passed into the sandbox (only `DJANGO_SETTINGS_MODULE` today) |
| `coverage_error` | why the execution has no coverage report, if it has none ([coverage.md](coverage.md)) |

The job lifecycle (`status`: queued → running → succeeded/failed) is separate from the test
verdict (`outcome`). A run whose tests fail is a **succeeded** job with outcome `failed`.

## Verified on real projects

All example repositories run green in the real sandbox (`tests/sandbox/test_suite_runner.py`):

| Example | Dependencies installed | Result |
|---|---|---|
| `discount_service` | none (base image) | 2 passed |
| `fastapi_todo` | fastapi, httpx, pydantic, pytest, uvicorn[standard] | 3 passed |
| `flask_notes` | flask, pytest, sqlalchemy | 2 passed (unittest-style, run by pytest) |
| `django_blog` | django, djangorestframework, psycopg[binary], pytest-django | 2 passed, with `DJANGO_SETTINGS_MODULE` detected |

The first run of `django_blog` failed with `no such table: posts_post`. The example had no
migration for its model, which `manage.py test` would also have reported. The runner found a
real defect in the example, and the missing migration was added.

## Running the sandbox from the worker

The sandbox needs a Docker daemon. When the worker runs on a host with Docker
(`make worker`, and in CI), executions and AI test validation run for real.

The **containerised** worker in `docker-compose.yml` deliberately has **no** access to the
Docker socket, because mounting it would give the worker root on the host
([ADR-0003](adr/0003-sandboxed-execution.md)). Its executions complete with outcome
`sandbox_unavailable`, and the smoke test checks exactly that. Safe access for the
containerised worker (socket proxy or rootless runtime) is planned for Phase 13.

## Orphaned jobs

Jobs are picked up only while `queued`, so a re-delivered message never runs a job twice. If
a worker dies after moving a job to `running`, the `worker_ready` signal handler marks every
analysis run, test generation and test execution that has been `running` for longer than
the Celery task time limit as `failed` ("The worker stopped while this job was running").
Celery would have killed such a task anyway.
