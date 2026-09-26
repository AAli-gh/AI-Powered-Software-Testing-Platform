"""Tests in this directory execute code in the real Docker sandbox (`make sandbox-image`)."""

from __future__ import annotations

import os

import pytest

from testmind.sandbox.docker_sandbox import DockerSandbox

SANDBOX_IMAGE = os.environ.get("TESTMIND_TEST_SANDBOX_IMAGE", "testmind-sandbox:local")


@pytest.fixture(scope="session")
def sandbox() -> DockerSandbox:
    box = DockerSandbox(SANDBOX_IMAGE, timeout_seconds=60)
    if not box.available():
        pytest.fail(
            f"Docker sandbox image {SANDBOX_IMAGE!r} is not available: run `make sandbox-image`"
        )
    return box
