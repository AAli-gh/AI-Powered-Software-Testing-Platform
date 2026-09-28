# Contributing to TestMind

Thank you for helping. This page covers how to set up, what a change needs before review, and
the rules the code base follows.

## Set up

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Docker with Compose v2, Node 22
(for the dashboard) and GNU Make.

```bash
make setup           # dependencies, .env from .env.example, pre-commit hooks
make test-services   # disposable PostgreSQL and Redis for the tests
make sandbox-image   # the image tests run in
make check           # lint, types, the whole Python suite with coverage: what CI runs
```

[docs/development.md](docs/development.md) explains running the stack, the API or the worker
on their own, and migrations.

## What a change needs

- **Tests.** Every behaviour change comes with tests, and every bug fix with a test that fails
  without the fix. Core infrastructure is not mocked. PostgreSQL, Redis, Celery and the Docker
  sandbox are real in the tests. Doubles are only for external boundaries: the LLM, GitHub
  (a local fake) and the task queue where a worker adds nothing ([docs/testing.md](docs/testing.md)).
- **The gates CI runs:**
  - Python: `make lint` (ruff), `make typecheck` (mypy `--strict` on `src`, `tests`,
    `migrations` and `scripts`), `make test` (CI requires 100 % line and branch coverage),
    `make security` (bandit, pip-audit) and gitleaks.
  - Dashboard: `make web-check`.
  - Prometheus: `make monitoring-check`.
  - The Docker job runs the stack with the sandbox and Prometheus, the smoke test, the
    monitoring check and the dashboard's browser tests.
- **API changes:** run `make openapi` so the committed schema and the dashboard's types match.
  CI fails otherwise.
- **Schema changes:** `make revision m="…"`, then read the generated migration. A test fails if
  models and migrations disagree, and another checks every downgrade.
- **Documentation:** update the page in `docs/` that describes the behaviour. For a decision
  with trade-offs, add an ADR in `docs/adr/` (copy the shape of an existing one).
- **Performance-sensitive changes** (queries over reports, list endpoints): measure with
  `scripts/load_test.py` ([docs/performance.md](docs/performance.md)) and put the numbers in
  the pull request.

## Rules of the code base

- **Deterministic first.** An LLM proposes, and deterministic tools decide. Nothing an LLM
  says is stored as a fact without a check: static checks, a sandbox run, or evidence compared
  with the source ([ADR-0006](docs/adr/0006-llm-proposes-tools-decide.md)).
- **No fake features.** A feature is either implemented and tested, or documented as not
  implemented. There are no stubs that pretend, and no TODOs in place of behaviour.
- **Repository code runs only in the sandbox.** Never import, execute or build a repository's
  code in the API or worker process ([docs/security.md](docs/security.md)).
- **Secrets stay in the environment.** Never commit keys or tokens. Settings that hold secrets
  are `SecretStr`. Never log secrets.
- **Layers:** routes stay thin, rules live in services, SQL lives in repositories
  ([docs/architecture.md](docs/architecture.md#dependency-rule)).
- **Metric labels** come from fixed sets only (route templates, task names, outcomes), never
  from user input ([docs/monitoring.md](docs/monitoring.md)).

## Commits and pull requests

- Small, focused commits. The message says what changed and why.
- The pull request says what was run to verify the change, and what was not verified.
- Security issues: report them privately through GitHub security advisories, not in an issue.

By contributing, you agree that your contributions are licensed under the [MIT license](LICENSE).
