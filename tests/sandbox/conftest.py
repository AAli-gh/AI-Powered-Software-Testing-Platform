"""Tests in this directory execute code in the real Docker sandbox (`make sandbox-image`)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker

from testmind.core.config import Settings
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.workers.celery_app import create_celery_app

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
