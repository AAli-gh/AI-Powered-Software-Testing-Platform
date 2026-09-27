"""Tests in this directory execute code in the real Docker sandbox (`make sandbox-image`)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker

from testmind.core.config import Settings
from testmind.mutation_engine.engine import MutationEngine
from testmind.mutation_engine.models import MutationReport
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.runner import TestSuiteRunner
from testmind.workers.celery_app import create_celery_app
from tests.sandbox.test_suite_runner import EXAMPLES, analyse

SANDBOX_IMAGE = os.environ.get("TESTMIND_TEST_SANDBOX_IMAGE", "testmind-sandbox:local")


@pytest.fixture(scope="session")
def sandbox() -> DockerSandbox:
    box = DockerSandbox(SANDBOX_IMAGE, timeout_seconds=60)
    if not box.available():
        pytest.fail(
            f"Docker sandbox image {SANDBOX_IMAGE!r} is not available: run `make sandbox-image`"
        )
    return box


@pytest.fixture
def celery_app(settings: Settings) -> Celery:
    app = create_celery_app(settings.model_copy(update={"sandbox_image": SANDBOX_IMAGE}))
    app.control.purge()
    return app


@pytest.fixture
def worker(celery_app: Celery) -> Iterator[None]:
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        yield


def mutation_engine(sandbox: DockerSandbox, **overrides: int) -> MutationEngine:
    options = {"max_mutants": 200, "mutant_timeout_cap": 30, "job_timeout_cap": 900, **overrides}
    runner = TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image))
    return MutationEngine(runner, sandbox, **options)  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def shipping(sandbox: DockerSandbox) -> MutationReport:
    repository, code = analyse(EXAMPLES / "shipping_rates")
    return mutation_engine(sandbox).run(EXAMPLES / "shipping_rates", repository, code)
