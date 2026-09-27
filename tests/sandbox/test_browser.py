"""Playwright browser tests in the browser sandbox image (docker/browser.Dockerfile).

The image needs Chromium and its system libraries downloaded while it is built. Where it
cannot be built this test is skipped with the reason; CI builds it and sets
TESTMIND_REQUIRE_BROWSER_IMAGE=1, which turns a missing image into a failure."""

import os

import pytest

from testmind.api_engine.models import Source
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner, uses_browser
from tests.sandbox.test_suite_runner import EXAMPLES, analyse

BROWSER_IMAGE = os.environ.get("TESTMIND_TEST_BROWSER_IMAGE", "testmind-sandbox-browser:local")


@pytest.fixture(scope="module")
def browser_image(sandbox: DockerSandbox) -> str:
    if not sandbox.available(BROWSER_IMAGE):
        message = f"browser sandbox image {BROWSER_IMAGE!r} is not built (make browser-image)"
        if os.environ.get("TESTMIND_REQUIRE_BROWSER_IMAGE") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return BROWSER_IMAGE


def test_playwright_tests_run_in_the_browser_sandbox(
    sandbox: DockerSandbox, browser_image: str
) -> None:
    """examples/web_counter/EXPECTED_BROWSER.md: a real Chromium, no network, the same
    isolation as every sandbox run; the page's requests are part of the API coverage."""
    root = EXAMPLES / "web_counter"
    repository, code = analyse(root)
    assert uses_browser(repository)
    builder = RunnerImageBuilder(sandbox.image, browser_image=browser_image, timeout_seconds=600)
    suite = TestSuiteRunner(sandbox, builder).run(root, repository, code=code)
    report = suite.report
    assert report.outcome is ExecutionOutcome.PASSED, report.output
    assert [t.name for t in report.tests] == [
        "test_page_title[chromium]",
        "test_clicking_increments_the_counter[chromium]",
    ]
    assert report.install is not None
    assert f"Playwright and a headless Chromium come from {browser_image}" in report.install.notes
    assert suite.api is not None
    assert [(e.method, e.path, e.source, e.statuses) for e in suite.api.endpoints] == [
        ("GET", "/", Source.BOTH, {"200": 2}),
        ("POST", "/api/increment", Source.BOTH, {"200": 2}),  # sent by the page's script
    ]
