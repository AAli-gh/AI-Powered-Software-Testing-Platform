# Changelog

All notable changes to TestMind are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). The HTTP API under `/api/v1` is the public
interface that semantic versioning applies to.

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
