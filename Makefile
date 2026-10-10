.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE      := docker compose
COMPOSE_TEST := docker compose -f docker-compose.test.yml
# The stack whose worker runs tests in the sandbox, through the sandbox proxy.
SANDBOX_DIR  ?= $(CURDIR)/.sandbox
PROMETHEUS_IMAGE := prom/prometheus:v3.5.0
COMPOSE_SANDBOX := TESTMIND_SANDBOX_DIR=$(SANDBOX_DIR) \
	TESTMIND_HOST_REPOS_DIR=$(abspath $(or $(TESTMIND_HOST_REPOS_DIR),examples)) \
	docker compose -f docker-compose.yml -f docker-compose.sandbox.yml

.PHONY: help
help: ## Show available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------- setup
.PHONY: setup
setup: ## Install dependencies and create .env from the template
	uv sync --locked
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	uv run pre-commit install
	cd web && npm ci --no-audit --no-fund

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

.PHONY: sandbox-image
sandbox-image: ## Build the Docker image used to run untrusted code (generated tests)
	docker build -f docker/sandbox.Dockerfile -t testmind-sandbox:local .

.PHONY: sandbox-images
sandbox-images: ## Build the sandbox image of every supported Python version (3.7-3.12)
	uv run python scripts/sandbox_images.py

.PHONY: browser-image
browser-image: sandbox-image ## Build the sandbox image with a headless Chromium (Playwright tests)
	docker build -f docker/browser.Dockerfile -t testmind-sandbox-browser:local .

.PHONY: test-unit
test-unit: ## Run unit tests only (no services, no Docker required)
	uv run pytest -m "not integration and not sandbox"

.PHONY: test
test: ## Full suite with coverage (needs `make test-services` and `make sandbox-image`)
	uv run pytest --cov --cov-report=term-missing --cov-report=xml

.PHONY: check
check: lint typecheck test ## Everything CI runs before building images

# ----------------------------------------------------------------- docker
.PHONY: up
up: ## Build and start the full stack
	$(COMPOSE) up -d --build --wait

.PHONY: up-sandbox
up-sandbox: sandbox-images ## The stack with sandboxed test execution (worker -> sandbox proxy -> Docker)
	mkdir -p $(SANDBOX_DIR)
	$(COMPOSE_SANDBOX) up -d --build --wait

.PHONY: down-sandbox
down-sandbox: ## Stop the sandbox stack
	$(COMPOSE_SANDBOX) down

.PHONY: up-monitoring
up-monitoring: ## The stack with Prometheus on 127.0.0.1:9090 (metrics and alerts)
	docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d --build --wait

.PHONY: monitoring-check
monitoring-check: ## Validate the Prometheus configuration and unit-test the alert rules
	docker run --rm --entrypoint promtool -v $(CURDIR)/docker/prometheus:/cfg:ro -w /cfg \
		-v /dev/null:/run/secrets/testmind_api_token:ro $(PROMETHEUS_IMAGE) check config prometheus.yml
	docker run --rm --entrypoint promtool -v $(CURDIR)/docker/prometheus:/cfg:ro -w /cfg \
		$(PROMETHEUS_IMAGE) test rules alerts.test.yml

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: logs
logs: ## Follow logs of all services
	$(COMPOSE) logs -f

.PHONY: worker
worker: ## Run a worker on the host (it can use Docker for the sandbox; see docs/test-engine.md)
	uv run celery -A testmind.workers.main worker --loglevel=INFO --concurrency=2

.PHONY: smoke
smoke: ## End-to-end smoke test against the running stack
	./scripts/smoke_test.sh

# ----------------------------------------------------------------- dashboard
.PHONY: openapi
openapi: ## Export the API schema and regenerate the dashboard's TypeScript types
	uv run python scripts/export_openapi.py
	cd web && npm run openapi

.PHONY: web-dev
web-dev: ## Dashboard dev server on :5173 (proxies the API on :8000)
	cd web && npm run dev

.PHONY: web-check
web-check: ## Dashboard lint, format, types, component tests with coverage, build
	uv run python scripts/export_openapi.py --check
	cd web && npm run lint && npm run format:check && npm run typecheck \
	  && npm run test:coverage && npm run build

.PHONY: web-e2e
web-e2e: ## Browser tests against a running stack (TESTMIND_WEB_URL, default :4173)
	cd web && npx playwright test

# ----------------------------------------------------------------- database
.PHONY: migrate
migrate: ## Apply migrations (local environment)
	uv run alembic upgrade head

.PHONY: revision
revision: ## Create a migration: make revision m="message"
	@test -n "$(m)" || (echo 'usage: make revision m="message"' && exit 1)
	uv run alembic revision --autogenerate -m "$(m)"
