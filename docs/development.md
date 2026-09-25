# Development

## Prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Docker with Compose v2 (for the test services and the full stack)
- GNU Make

## First-time setup

```bash
make setup            # uv sync --locked, create .env from .env.example, install pre-commit hooks
make test-services    # disposable PostgreSQL + Redis for tests
make check            # lint + type check + tests: the same gates as CI
```

## Running the stack

```bash
make up       # build images, start everything, wait until healthy
make smoke    # end-to-end check
make logs     # follow logs
make down     # stop (data volumes are kept; `docker compose down -v` removes them)
```

### Running the API or worker outside Docker

Point the `TESTMIND_*` variables in `.env` at reachable services (for example
`make test-services` plus a database created for development), then:

```bash
uv run alembic upgrade head
uv run uvicorn --factory testmind.api.app:create_app --reload
uv run celery -A testmind.workers.main worker --loglevel=INFO
```

## Database migrations

Models live in `src/testmind/db/models/`. After changing them:

```bash
make revision m="describe the change"   # autogenerate into migrations/versions/
```

**Always review the generated file.** Alembic's autogenerate has known gaps. For example, it
emits a duplicate `CHECK` constraint for enum columns (see the note in the first migration).
`tests/integration/test_migrations.py` fails if the models and migrations disagree, and checks
that every migration can be downgraded.

## Conventions

- **Layers:** routes stay thin, rules live in services, SQL lives in repositories. See
  [architecture.md](architecture.md#dependency-rule).
- **Errors:** raise a `core.errors.AppError` subclass from services and map it to HTTP in
  `api/errors.py`. Never raise `HTTPException` from services.
- **Typing:** `mypy --strict` must pass for `src`, `tests` and `migrations`.
- **Style:** `ruff format` and `ruff check` (config in `pyproject.toml`). Absolute imports only.
- **Logging:** `get_logger(__name__)` with event-style messages and key-value context
  (`logger.info("run_started", run_id=...)`). Never log secrets or connection URLs.
- **Commits:** small, focused and descriptive. The pre-commit hooks run ruff, mypy and gitleaks.

## Make targets

Run `make help` for the full list.
