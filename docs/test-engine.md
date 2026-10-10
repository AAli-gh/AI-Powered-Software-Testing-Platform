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
- **pytest, coverage.py and pytest-cov** are TestMind's own tools: its plugins and coverage
  measurement need the image's versions. The project's requirements for them, and for
  pytest's own dependencies (pluggy, py, iniconfig), are dropped
  (with a note) and the image's versions are pinned in the list, so an old
  `pytest==3.2.1` can neither downgrade them nor can another package upgrade them; a package
  that needs other versions fails the installation with pip's reason.
- The project itself is **not** installed. It is importable through `PYTHONPATH`
  (`/workspace/src` and `/workspace/src/src` for `src/` layouts).
- Images are tagged `testmind-runner:<hash>` and reused while the requirement list **and the
  base image's content id** are unchanged. Rebuilding the sandbox image (for example to
  upgrade pytest or coverage.py) therefore never reuses runner images built on the old one.

## Python versions

A project's tests run on the Python it declares, not on whatever TestMind happens to use.
There is one sandbox image per version from 3.7 to 3.12 (`make sandbox-images`). Each has the
newest pytest, coverage.py and pytest-cov that support that version; the pins are in
`SANDBOX_PYTHONS` (`src/testmind/sandbox/python_versions.py`).

**What the project declares.** The repository analysis reads the project's root files
(`analyzers/repository/python_version.py`). Nothing is executed:

| Source | Example | Meaning |
|---|---|---|
| `.python-version`, `runtime.txt` | `3.8.10`, `python-3.8.10` | pinned |
| `requires-python` (pyproject), `python_requires` (setup.cfg, setup.py), Poetry's `python` | `>=3.7,<3.11`, `^3.8` | allowed range |
| tox's `envlist`, trove classifiers | `py{37,38}-django22`, `Programming Language :: Python :: 3.8` | tested on |

**Which version it runs on**, among those with an image (`TESTMIND_SANDBOX_PYTHON_VERSIONS`):

1. the pinned version;
2. otherwise the newest version it is tested on, within the allowed range;
3. when the versions it names (pinned, tested) have no image, the **closest newer** version
   allowed: a project tested up to 3.5 runs on 3.7, not 3.12, because it is far more likely
   to work there;
4. otherwise the newest version allowed;
5. if nothing fits, the default (3.12), and the notes say its tests may fail for that reason.

The choice and its reason are the first install note of every execution, for example
`Python 3.8: newest version the project is tested on (3.5, 3.6, 3.7, 3.8)`.

Python 3.6 and older are not supported: TestMind's API-capture plugin needs `contextvars`
(3.7). Browser tests always run on the browser image, which has Python 3.12.

`tests/sandbox/test_python_versions.py` runs a project pinned to each version on its image.
The test checks the interpreter inside the container, coverage, failure capture and the
mutation driver. CI builds every image and fails if one is missing.

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

The **containerised** worker never gets the Docker socket, because mounting it would give the
worker root on the host ([ADR-0003](adr/0003-sandboxed-execution.md)). With
`docker-compose.yml` alone its executions complete with outcome `sandbox_unavailable`.

With the sandbox override (`make up-sandbox`, `docker-compose.sandbox.yml`) the worker talks
to Docker through `sandbox-proxy`, which allows only TestMind's locked-down sandbox
containers and runner-image builds ([ADR-0014](adr/0014-policy-enforcing-docker-proxy.md),
[security.md](security.md#sandbox-proxy)). CI runs the smoke test on that stack with
`TESTMIND_EXPECT_SANDBOX=1`, which fails if any step reports `sandbox_unavailable`.

## Orphaned jobs

Jobs are picked up only while `queued`, so a re-delivered message never runs a job twice. If
a worker dies after moving a job to `running`, the `worker_ready` signal handler marks every
job of every kind that has been `running` for longer than the Celery task time limit as
`failed` ("The worker stopped while this job was running").
Celery would have killed such a task anyway.
