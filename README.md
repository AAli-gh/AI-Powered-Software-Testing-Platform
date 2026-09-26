# TestMind — AI-Powered Software Testing Platform

[![CI](https://github.com/AAli-gh/AI-Powered-Software-Testing-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AAli-gh/AI-Powered-Software-Testing-Platform/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![License](https://img.shields.io/badge/license-MIT-green)

TestMind is a platform that takes a software repository, understands its structure and code,
generates and validates tests, runs them in isolation, measures coverage and mutation score,
explains failures and regressions, and produces a quality report.

It is **an intelligence layer on top of standard testing tools** (pytest, coverage.py, git,
Playwright), not a replacement for them. Deterministic tools do everything they can do reliably;
LLMs are used only where they add real value: writing tests, hypothesising root causes, and
summarising findings. Every AI output comes with its evidence and a confidence level.

> **Status: Phases 1–5 of 14 are complete: the platform foundation, the deterministic
> repository and code analyzers, AI test generation with sandboxed validation, and the
> sandboxed test runner.**
> The AI stage has been verified end to end with a scripted model. A live call to the
> Anthropic API has not been run yet because no API key was available (see
> [docs/ai-engine.md](docs/ai-engine.md)). The [roadmap](#roadmap) shows exactly what exists today and what is
> planned.

---

## Contents

- [Problem](#problem) · [Solution](#solution) · [Architecture](#architecture)
- [What works today](#what-works-today) · [Tech stack](#tech-stack)
- [Quick start](#quick-start-docker) · [Configuration](#configuration) · [Usage](#usage)
- [API](#api) · [Development & testing](#development--testing) · [CI/CD](#cicd)
- [Security](#security) · [Roadmap](#roadmap) · [Documentation](#documentation)

## Problem

Writing good tests is slow, and it is hard to tell whether a test suite actually protects you.
High line coverage can hide tests that assert nothing. When CI goes red, engineers spend time
reading stack traces to work out which change broke what. Most "AI testing" tools generate code
without running it, never validate it, and state guesses as facts.

## Solution

A pipeline in which every stage is either deterministic or checked by a deterministic stage:

```text
Repository → Repository Analyzer → Code Analyzer (AST, static analysis)
           → AI Test Generator → Validation loop (syntax → run → analyse → fix, bounded)
           → Test Runner (pytest, sandboxed) → Coverage Analyzer (coverage.py)
           → Failure Analyzer (AI, evidence-based) → Regression Analyzer (git diff, test selection)
           → Mutation Testing → AI Report Generator → Dashboard / GitHub integration
```

## Architecture

```mermaid
flowchart LR
    client([Client / Dashboard]) -->|HTTP| api[FastAPI API]
    api -->|SQL| pg[(PostgreSQL)]
    api -->|enqueue| redis[(Redis broker)]
    redis --> worker[Celery worker]
    worker -->|status + results| pg
    worker -->|read-only| repos[/Repositories root/]
    api -->|validate path| repos
```

The backend follows a layered (clean) architecture. Dependencies point only downwards, and the API
and worker share the same service layer:

```text
api/routes  →  services  →  repositories  →  db/models
workers/    ↗      ↘  domain (pure: enums, run state machine)
```

Details: [docs/architecture.md](docs/architecture.md). Design decisions are recorded as
[ADRs](docs/adr/).

## What works today

Everything listed here is implemented and tested.

- **Projects API**: register a local repository and list, get or delete projects. Paths are
  resolved and confined to a configured root, so `../`, absolute paths and symlinks that
  escape the root are rejected.
- **Analysis runs**: queued through **Celery + Redis** and executed by a separate worker. They
  follow an explicit state machine (`queued → running → succeeded | failed`, `queued → cancelled`)
  enforced with row locks, and the worker is idempotent under at-least-once delivery.
- **Repository analysis** (every run): a deterministic profile of the repository, computed
  without an LLM and without executing any repository code
  ([details](docs/repository-analyzer.md)):
  - languages and line counts, and the file inventory (respecting `.gitignore`, never
    following symlinks);
  - dependencies from `pyproject.toml` (PEP 621, PEP 735, Poetry), `requirements*.txt` (with
    `-r` includes), `setup.cfg`, static `setup.py` and `Pipfile`, grouped into runtime,
    development and optional;
  - **Django, FastAPI, Flask, pytest and unittest** detection. Each detection has a
    **confidence** score and the **evidence** behind it, taken from the AST, never from text
    matching;
  - test files, entry points (console scripts, `manage.py`, `__main__`, ASGI/WSGI apps
    including app factories), config files, Docker (base images, compose services) and CI/CD
    (GitHub Actions, GitLab CI, CircleCI, …).
- **Code analysis** (every run, AST only, [details](docs/code-analyzer.md)):
  - functions, methods, classes and parameters (kinds, type hints, defaults), raised
    exceptions, and module imports with relative imports resolved;
  - **cyclomatic complexity** per function, with an A–F rank;
  - **19 rules** with severity, confidence and the offending line as evidence: dangerous
    patterns (`eval`, `pickle`, `shell=True`, unsafe `yaml.load`, hard-coded secrets), error
    handling (bare or swallowed exceptions), potential bugs (mutable defaults, `is` with
    literals, requests without timeout), dead code, high complexity, and heuristic
    missing-validation checks (clearly marked with low confidence);
  - suppression per line with `# testmind: ignore[TM0xx]`, and `# noqa` for the equivalent
    flake8 codes.
- **AI test generation** ([details](docs/ai-engine.md)), requested per run:
  - deterministic target selection (public functions, prioritised by findings and
    complexity) and one pytest module per source module;
  - versioned prompt files; a provider-neutral LLM interface with an **Anthropic**
    implementation (`claude-opus-5`, server-side refusal fallback);
  - a **bounded validate → run → fix loop**: static checks, then a real pytest run in a
    **Docker sandbox** (no network, read-only, non-root, CPU/memory/pids limits, timeout),
    with concrete failures fed back to the model;
  - tests that fail because of a real defect are kept as `xfail(strict=True)` and
    reported as **suspected bugs** instead of being weakened;
  - without an API key the endpoint answers `503 llm_not_configured`. It never pretends to
    generate anything.
- **Test execution** ([details](docs/test-engine.md)): runs the repository's own test suite,
  optionally plus generated tests, in the sandbox:
  - the project's dependencies are installed into a cached **runner image**, **wheels
    only**, so no third-party `setup.py` ever runs;
  - Django projects get `DJANGO_SETTINGS_MODULE`, detected from the AST;
  - a structured report: outcome, pytest exit code and its meaning, totals, duration, and
    every test with file, line, message and traceback, plus logs;
  - verified green on the Django, FastAPI, Flask and plain-pytest example repositories.
- **Orphaned-job recovery:** jobs left `running` by a crashed worker are failed when a worker
  starts.
- **Operations**: liveness (`/health`) and readiness (`/ready`, real DB + Redis checks) probes,
  structured JSON logs with request-ID correlation, uniform error responses and OpenAPI docs.
- **Infrastructure**: PostgreSQL schema managed by Alembic (with a test that fails on
  model/migration drift), Docker images (non-root, read-only filesystem, all capabilities
  dropped), docker-compose for the full stack, and a GitHub Actions pipeline.
- **Test suite**: 670 tests with 100 % line and branch coverage in CI. They run against real
  PostgreSQL and Redis (no database mocks) and against golden example repositories for
  Django, FastAPI and Flask, plus an intentionally flawed repository whose documented
  defects must be found exactly. An end-to-end smoke test runs against the containers.

## Tech stack

| Area | Technology |
|---|---|
| Language | Python 3.11+ (images use 3.12) |
| API | FastAPI, Pydantic v2 |
| Persistence | PostgreSQL 16, SQLAlchemy 2 (async, asyncpg), Alembic |
| Background jobs | Celery, Redis |
| Observability | structlog (JSON), request IDs, health/readiness probes |
| Quality | pytest, pytest-asyncio, coverage.py, ruff, mypy (strict) |
| AI | Anthropic SDK (Claude), versioned prompt files, provider-neutral interface |
| Security | bandit, pip-audit, gitleaks, non-root read-only containers, Docker sandbox for untrusted code |
| Packaging / infra | uv, Docker (multi-stage), docker compose, GitHub Actions |
| Planned | coverage.py & mutation testing engines, LLM providers (pluggable), Playwright, GitHub API, React dashboard |

## Quick start (Docker)

Requirements: Docker with Compose v2.

```bash
git clone https://github.com/AAli-gh/AI-Powered-Software-Testing-Platform.git
cd AI-Powered-Software-Testing-Platform
cp .env.example .env          # then change POSTGRES_PASSWORD
make up                       # build + start postgres, redis, migrate, api, worker (waits for healthy)
make smoke                    # end-to-end check against the running stack
```

The API listens on <http://127.0.0.1:8000>. Interactive docs are at <http://127.0.0.1:8000/docs>.
`make down` stops everything.

## Configuration

All settings are environment variables with the `TESTMIND_` prefix (see
[`.env.example`](.env.example)). Secrets live only in `.env`, which is git-ignored.

| Variable | Default | Purpose |
|---|---|---|
| `TESTMIND_ENV` | `development` | `development` / `test` / `production` |
| `TESTMIND_LOG_LEVEL` | `INFO` | Log level |
| `TESTMIND_LOG_JSON` | `true` | JSON logs (`false` = human-readable) |
| `TESTMIND_DATABASE_URL` | — | `postgresql+asyncpg://…` (built automatically in docker-compose) |
| `TESTMIND_REDIS_URL` | — | `redis://…` |
| `TESTMIND_REPOS_ROOT` | — | Directory that projects must live in (`/repos` in containers) |
| `TESTMIND_HOST_REPOS_DIR` | `./examples` | Host directory mounted read-only at `/repos` |
| `TESTMIND_CELERY_TASK_TIME_LIMIT_SECONDS` | `3600` | Hard time limit per job |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | — | Database container credentials |

## Usage

Register one of the bundled [example repositories](examples/), queue a run and read the
analysis:

```bash
curl -s -X POST localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name": "blog", "source_location": "django_blog"}'
# → 201 {"id": "…", "name": "blog", "source_type": "local", "source_location": "django_blog", …}

curl -s -X POST localhost:8000/api/v1/projects/<project-id>/runs
# → 202 {"id": "…", "status": "queued", …}

curl -s localhost:8000/api/v1/runs/<run-id>
# → 200 {"status": "succeeded", "started_at": "…", "finished_at": "…", …}

curl -s localhost:8000/api/v1/runs/<run-id>/analysis
```

Output for `examples/django_blog` (abridged, real output):

```json
{
  "file_count": 15,
  "primary_language": "Python",
  "frameworks": [
    {"name": "Django", "category": "web_framework", "confidence": 1.0, "evidence": [
      "'django' declared in requirements.txt",
      "'django' imported in 7 file(s): blog_project/asgi.py, blog_project/urls.py, blog_project/wsgi.py (+4 more)",
      "DJANGO_SETTINGS_MODULE set in blog_project/asgi.py, blog_project/wsgi.py, manage.py; INSTALLED_APPS defined in blog_project/settings.py; …"
    ]}
  ],
  "test_frameworks": [
    {"name": "unittest", "confidence": 0.7, "evidence": ["1 TestCase subclass(es) in posts/tests.py"]},
    {"name": "pytest", "confidence": 0.5, "evidence": ["'pytest' declared in requirements-dev.txt"]}
  ],
  "dependencies": [
    {"name": "pytest", "specifier": ">=8", "group": "development", "source": "requirements-dev.txt"},
    {"name": "django", "specifier": "<6,>=5.0", "group": "runtime", "source": "requirements.txt"}
  ],
  "test_files": ["posts/tests.py"],
  "entry_points": [
    {"kind": "asgi_app", "path": "blog_project/asgi.py", "target": "blog_project.asgi:application"},
    {"kind": "django_manage", "path": "manage.py", "target": null},
    {"kind": "wsgi_app", "path": "blog_project/wsgi.py", "target": "blog_project.wsgi:application"}
  ],
  "docker": {"dockerfiles": ["Dockerfile"], "base_images": ["python:3.12-slim"], "compose_files": [], "compose_services": []},
  "ci": [],
  "issues": []
}
```

Errors always have the same shape:

```json
{"error": {"code": "invalid_source", "message": "Source location '../../etc' must be a directory inside the repositories root"}}
```

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness (no dependency checks) |
| `GET` | `/ready` | Readiness: PostgreSQL + Redis (`503` if either is down) |
| `POST` | `/api/v1/projects` | Register a project (`201`, `409` duplicate name, `422` invalid source) |
| `GET` | `/api/v1/projects` | List projects (`limit` ≤ 100, `offset`) |
| `GET` | `/api/v1/projects/{id}` | Get a project |
| `DELETE` | `/api/v1/projects/{id}` | Delete (`409` while runs are queued/running) |
| `POST` | `/api/v1/projects/{id}/runs` | Queue an analysis run (`202`, `503` if the queue is down) |
| `GET` | `/api/v1/projects/{id}/runs` | List a project's runs |
| `GET` | `/api/v1/runs/{id}` | Get a run |
| `POST` | `/api/v1/runs/{id}/cancel` | Cancel a queued run (`409` once started) |
| `GET` | `/api/v1/runs/{id}/analysis` | Repository analysis summary of a succeeded run |
| `GET` | `/api/v1/runs/{id}/analysis/files` | Analysed files (`limit` ≤ 500, `offset`, `language`, `is_test`) |
| `GET` | `/api/v1/runs/{id}/code` | Code metrics: size, complexity distribution, findings by severity/category |
| `GET` | `/api/v1/runs/{id}/code/findings` | Findings, most severe first (`min_severity`, `category`, `rule_id`, `min_confidence`, `path_prefix`) |
| `GET` | `/api/v1/runs/{id}/code/functions` | Functions/methods (`sort=complexity`, `min_complexity`, `public_only`, `path_prefix`) |
| `GET` | `/api/v1/runs/{id}/code/classes` · `/code/modules` | Classes; modules with resolved imports |
| `GET` | `/api/v1/code-rules` | Catalog of code-analysis rules |
| `POST` | `/api/v1/runs/{id}/test-generations` | Queue AI test generation (`202`; `409` if the run has not succeeded; `503` if no LLM is configured) |
| `GET` | `/api/v1/runs/{id}/test-generations` · `/api/v1/test-generations/{id}` | Generation status and result (generated code, statuses, suspected bugs) |
| `POST` | `/api/v1/runs/{id}/test-executions` | Queue a sandboxed run of the test suite (`{"generation_id": …}` adds generated tests) |
| `GET` | `/api/v1/test-executions/{id}` · `/tests` · `/logs` | Summary; per-test results (`outcome`, `generated` filters); pytest and install logs |

Full reference: [docs/api.md](docs/api.md) and the live OpenAPI schema at `/docs`.

## Development & testing

```bash
make setup           # uv sync + .env + pre-commit hooks
make test-services   # throw-away PostgreSQL + Redis on localhost:55432 / 56379
make sandbox-image   # Docker image in which generated tests are executed
make check           # ruff + mypy --strict + full test suite with coverage (gate: 85 %)
make help            # all targets
```

| Suite | Location | Runs against |
|---|---|---|
| Unit | `tests/unit/` | Pure code: config, state machine, path confinement, Celery config, every analyzer detector and rule (positive and negative cases), golden tests on `examples/`, the generator loop and prompt handling |
| Sandbox | `tests/sandbox/` | The real Docker sandbox: isolation guarantees, timeouts, and the full generate → run → fix loop |
| Integration | `tests/integration/` | Real PostgreSQL + Redis + an in-process Celery worker |
| API | `tests/api/` | The FastAPI app over HTTP (httpx) with a real database |
| Smoke / E2E | `scripts/smoke_test.sh` | The running docker-compose stack |

Only the task queue boundary has a test double, and only in tests that do not need a worker.
The real Celery path is covered end to end. See [docs/testing.md](docs/testing.md) and
[docs/development.md](docs/development.md).

## CI/CD

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push and pull request:

```text
lint (ruff) ─┐
typecheck ───┼─► docker: build images → compose up (healthy) → smoke test
tests ───────┘     (Python 3.11 + 3.12, real Postgres/Redis, coverage gate)
security: bandit · pip-audit (locked deps) · gitleaks (full git history)
```

## Security

TestMind will eventually execute code from the repositories it analyses, so isolation is part of
the design from the start. See [docs/security.md](docs/security.md) for details.

In place today:
- Secrets only in environment variables. `.env` is git-ignored and connection URLs are
  `SecretStr`, so they never appear in logs or reprs.
- Project paths are confined to the repositories root (traversal and symlink escapes are
  rejected), and repositories are mounted **read-only**.
- Containers run as a non-root user with a read-only root filesystem, `no-new-privileges` and
  every Linux capability dropped. PostgreSQL and Redis are not published to the host, and the
  API binds to `127.0.0.1` only.
- The Celery broker accepts JSON only (no pickle).
- CI runs bandit, pip-audit and gitleaks.

- **Untrusted code runs only in a Docker sandbox** with no network, all capabilities dropped,
  `no-new-privileges`, user `nobody`, a read-only root filesystem and a read-only repository,
  CPU/memory/pids limits and a hard timeout. The JUnit output is parsed with `defusedxml`.
- LLM output is never trusted: static checks and a sandboxed pytest run decide
  ([ADR-0006](docs/adr/0006-llm-proposes-tools-decide.md)).

- Project dependencies are installed **wheels only** (`pip --only-binary=:all:`), so no
  third-party build scripts run.

Still open: giving the **containerised** worker access to Docker for the sandbox. It needs a
socket proxy or rootless runtime, not the raw Docker socket. Until then, the compose stack
reports `sandbox_unavailable` for executions and `unverified` for generated tests, while a
worker on a Docker host (`make worker`, CI) runs them for real. This is planned for
Phase 13 ([ADR-0003](docs/adr/0003-sandboxed-execution.md)).

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture: stack, structure, Docker, PostgreSQL, Redis, backend skeleton, CI | ✅ Done |
| 2 | Repository analyzer: languages, frameworks, file tree, dependencies, metadata | ✅ Done |
| 3 | Code analyzer: AST, functions/classes, complexity, static analysis | ✅ Done |
| 4 | Test generator: unit / edge / negative tests, bounded validation loop | ✅ Done (live Anthropic call not yet verified: no API key available) |
| 5 | Test runner: sandboxed pytest, structured results, tracebacks | ✅ Done |
| 6 | Coverage: coverage.py, file/function/branch coverage, weak areas | Planned |
| 7 | AI failure analyzer: evidence, root-cause hypothesis, confidence | Planned |
| 8 | Regression engine: git diff, affected tests, test selection | Planned |
| 9 | Mutation testing: mutants, score, survivors | Planned |
| 10 | API & E2E testing: endpoint discovery, API tests, Playwright | Planned |
| 11 | GitHub integration: import, PR analysis, comments, issue suggestions | Planned |
| 12 | Dashboard: overview, tests, coverage, findings, regressions, mutation | Planned |
| 13 | Security hardening: sandboxing, limits, network restrictions | Planned |
| 14 | Production quality: monitoring, performance, complete docs | Planned |

## Documentation

- [Architecture](docs/architecture.md): layers, run lifecycle, data model, known limitations
- [Repository analyzer](docs/repository-analyzer.md): detectors, confidence model, safety limits
- [Code analyzer](docs/code-analyzer.md): symbols, complexity, rule catalog, suppression
- [AI engine](docs/ai-engine.md): test generation pipeline, providers, prompts, sandbox validation
- [Test engine](docs/test-engine.md): runner images, sandboxed execution, reports, orphaned jobs
- [API](docs/api.md): endpoints, error model, pagination
- [Testing](docs/testing.md): test strategy and how to run each suite
- [Development](docs/development.md): local setup, migrations, conventions
- [Security](docs/security.md): threat model and controls
- [Architecture Decision Records](docs/adr/)

## License

[MIT](LICENSE)
