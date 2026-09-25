.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE      := docker compose
COMPOSE_TEST := docker compose -f docker-compose.test.yml

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- setup
.PHONY: setup
setup: ## Install dependencies and create .env from the template
	uv sync --locked
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	uv run pre-commit install

# ----------------------------------------------------------------- quality
.PHONY: lint
lint: ## Run ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

.PHONY: format
format: ## Auto-format and auto-fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

.PHONY: typecheck
typecheck: ## Run mypy (strict)
	uv run mypy

.PHONY: security
security: ## Run bandit and audit the locked dependencies for known CVEs
	uv run bandit -c pyproject.toml -r src
	@req=$$(mktemp) && trap 'rm -f "$$req"' EXIT && \
	  uv export --frozen --no-emit-project --all-groups --format requirements-txt -o "$$req" >/dev/null && \
	  uv run pip-audit --disable-pip --strict --progress-spinner off -r "$$req"

# ----------------------------------------------------------------- tests
.PHONY: test-services
test-services: ## Start PostgreSQL + Redis used by the test suite
	$(COMPOSE_TEST) up -d --wait

.PHONY: test-services-down
test-services-down: ## Stop the test PostgreSQL + Redis
	$(COMPOSE_TEST) down -v

.PHONY: test-unit
test-unit: ## Run unit tests only (no services required)
	uv run pytest -m "not integration"

.PHONY: test
test: ## Run the full test suite with coverage (needs `make test-services`)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: check
check: lint typecheck test ## Everything CI runs before building images

# ----------------------------------------------------------------- docker
.PHONY: up
up: ## Build and start the full stack
	$(COMPOSE) up -d --build --wait

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: logs
logs: ## Follow logs of all services
	$(COMPOSE) logs -f

.PHONY: smoke
smoke: ## End-to-end smoke test against the running stack
	./scripts/smoke_test.sh

# ----------------------------------------------------------------- database
.PHONY: migrate
migrate: ## Apply migrations (local environment)
	uv run alembic upgrade head

.PHONY: revision
revision: ## Create a migration: make revision m="message"
	@test -n "$(m)" || (echo 'usage: make revision m="message"' && exit 1)
	uv run alembic revision --autogenerate -m "$(m)"
