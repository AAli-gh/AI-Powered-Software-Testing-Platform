# Changelog

All notable changes to TestMind are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The HTTP API under `/api/v1` is the public
interface that semantic versioning applies to.

## [Unreleased]

### Added

- One sandbox image per Python version from 3.7 to 3.12 (`make sandbox-images`). Each
  project runs on the version it declares (`.python-version`, `requires-python`, tox,
  classifiers), or the closest newer one.
- An evaluation on 135 real bugs from BugsInPy ([docs/evaluation.md](docs/evaluation.md)), with
  its harness (`benchmarks/bugsinpy/`) and per-bug results.
- Spectrum-based fault localisation ([docs/fault-localization.md](docs/fault-localization.md)):
  every failure group, and a `/failures/suspects` endpoint, rank the functions most likely to
  hold the bug, from the traceback and the program spectrum. Deterministic, no LLM. On BugsInPy
  it ranks the changed function 1st in 33 of 65 reproduced bugs (top 5 in 49), against 9 for
  the traceback alone.
- External AI is now off by default and switchable per feature (`TESTMIND_LLM_PROVIDER`,
  `TESTMIND_AI_FEATURES`); detection never depends on it.

### Fixed

Found by the evaluation:

- `setup.py`: set literals and names bound once to a literal are read.
- `test_requirements.txt` and `*-requirements.txt` are read.
- flit's legacy `[tool.flit.metadata]` is read.
- A project's own `pytest`, `coverage`, `pytest-cov`, `pluggy` or `iniconfig` requirement no
  longer replaces the sandbox's.
- unittest assertion failures are `assertion_failure`, not `exception_in_library`.
- 16 settings documented in `.env.example` were not passed to the containers by Docker
  Compose; nor was the browser image setting, with the sandbox overlay.

## [1.0.0] - 2026-09-28

The first release: the fourteen phases of the [roadmap](README.md#roadmap).

### Added

- **Platform:**
  - FastAPI API, Celery worker, PostgreSQL (Alembic migrations) and Redis.
  - An explicit job state machine with row locks, idempotent under at-least-once delivery.
  - Orphaned jobs failed at worker start.
  - Structured JSON logs with request ids; liveness and readiness probes; Docker Compose.
- **Analysis:**
  - Deterministic repository analysis: languages, frameworks, dependencies, entry points,
    with evidence and confidence.
  - Code analysis: symbols, complexity, a rule catalog of defects.
- **AI test generation**, validated in the sandbox with a bounded fix loop. Coverage-guided
  generation targets the riskiest gaps.
- **Test execution** in an isolated Docker sandbox: no network, non-root, read-only, limits,
  timeout.
  - Structured per-test results.
  - Coverage mapped onto functions, cross-checked against coverage.py.
- **Failure analysis:** deterministic grouping and evidence. AI root-cause hypotheses are
  kept only when their cited evidence is verified.
- **Regression analysis** between git commits (read with dulwich): AST diff, test selection
  with reasons, and a comparison of both runs.
- **Mutation testing:**
  - an own mutant generator and in-sandbox driver;
  - scores per function;
  - AI tests for surviving mutants, accepted only if they pass on the original and kill the
    mutant.
- **API testing:**
  - endpoint discovery for FastAPI, Flask and Django;
  - API coverage observed inside the application;
  - deterministic probes;
  - AI API tests.
  - Existing Playwright suites run in a browser sandbox image.
- **GitHub App:**
  - signed webhooks;
  - pull request analysis posted as a check run and one comment;
  - issue suggestions;
  - repository import.
- **Dashboard:**
  - React and TypeScript, typed from the OpenAPI schema;
  - served by an unprivileged nginx with a strict CSP;
  - component and browser tests.
- **Security:**
  - the containerised worker reaches Docker only through a policy-enforcing proxy;
  - a seccomp profile stricter than Docker's default;
  - an API token (required in production) and rate limiting of changes;
  - secrets redacted from every LLM prompt.
- **Monitoring:**
  - Prometheus metrics for HTTP requests, jobs by status, the oldest waiting and running job,
    the queue, worker tasks, sandbox runs, and LLM requests and tokens;
  - ten alert rules with unit tests;
  - a monitoring compose overlay.
- **Performance:**
  - a load-test tool;
  - the platform overview answers in 0.19 s instead of 11.4 s, and a run's execution list in
    63 ms instead of 318 ms, on 5,000 executions (p50 with 10 requests in flight).
- **Documentation:**
  - architecture, every engine, API, security, monitoring, performance, deployment,
    contributing;
  - fifteen architecture decision records.

### Known limitations

- One shared API token; no user accounts, roles or per-user audit trail.
- Sandboxes share the host kernel; no gVisor or other user-space kernel.
- The GitHub App was tested against a local fake of the GitHub API, not against github.com.
- The AI stages were verified end to end with a scripted model. A live call to the Anthropic
  API has not been run: no API key was available.
- One host: no high availability for PostgreSQL or Redis.

[1.0.0]: https://github.com/AAli-gh/AI-Powered-Software-Testing-Platform/releases/tag/v1.0.0
