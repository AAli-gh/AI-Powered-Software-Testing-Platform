"""The test runner against the real Docker daemon: dependency images and real test suites."""

import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from testmind.analyzers.repository import RepositoryAnalyzer
from testmind.analyzers.repository.models import Dependency, DependencyGroup
from testmind.sandbox.docker_sandbox import DockerSandbox, PytestJob
from testmind.sandbox.models import Outcome
from testmind.sandbox.runner_image import DependencyInstallError, RunnerImageBuilder
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def dependency(name: str, specifier: str = "") -> Dependency:
    return Dependency(name=name, specifier=specifier, group=DependencyGroup.RUNTIME, source="r.txt")


@pytest.fixture(scope="module")
def builder(sandbox: DockerSandbox) -> Iterator[RunnerImageBuilder]:
    """A builder on a per-session alias of the sandbox image, so every run really builds
    (and coverage does not depend on images cached by an earlier run). Cleaned up after."""
    alias = f"testmind-sandbox:test-{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "tag", sandbox.image, alias], check=True, capture_output=True)  # noqa: S603, S607
    image_builder = RunnerImageBuilder(alias, timeout_seconds=600)
    yield image_builder
    built = [image_builder.tag_for(r) for r in BUILT_REQUIREMENTS]
    subprocess.run(["docker", "rmi", "--force", alias, *built], check=False, capture_output=True)  # noqa: S603, S607


BUILT_REQUIREMENTS = [["six==1.16.0"], ["pyyaml>=6.0", "requests>=2.31"]]


@pytest.fixture(scope="module")
def runner(sandbox: DockerSandbox, builder: RunnerImageBuilder) -> TestSuiteRunner:
    return TestSuiteRunner(sandbox, builder)


def test_image_is_built_once_then_reused(
    builder: RunnerImageBuilder, sandbox: DockerSandbox
) -> None:
    first = builder.ensure([dependency("six", "==1.16.0")])
    second = builder.ensure([dependency("six", "==1.16.0")])
    assert first.tag == second.tag
    assert second.from_cache is True
    assert sandbox.available(first.tag)

    tests = {
        "test_six.py": "import six\n\ndef test_six():\n    assert six.__version__ == '1.16.0'\n"
    }
    repo = EXAMPLES / "discount_service"
    result = sandbox.run(repo, PytestJob(test_files=tests, image=first.tag))
    assert [t.outcome for t in result.tests] == [Outcome.PASSED]


def test_unknown_package_fails_with_a_log(builder: RunnerImageBuilder) -> None:
    with pytest.raises(DependencyInstallError) as caught:
        builder.ensure([dependency("testmind-package-that-does-not-exist-xyz")])
    assert "testmind-package-that-does-not-exist-xyz" in caught.value.log


@pytest.mark.parametrize(
    ("example", "total", "environment"),
    [
        ("discount_service", 2, {}),
        ("fastapi_todo", 3, {}),
        ("flask_notes", 2, {}),
        ("django_blog", 2, {"DJANGO_SETTINGS_MODULE": "blog_project.settings"}),
    ],
)
def test_example_suites_pass(
    runner: TestSuiteRunner, example: str, total: int, environment: dict[str, str]
) -> None:
    root = EXAMPLES / example
    report = runner.run(root, RepositoryAnalyzer().analyze(root))
    assert report.outcome is ExecutionOutcome.PASSED, report.output
    assert report.totals.total == total
    assert report.totals.passed == total
    assert report.environment == environment
    assert all(t.file and t.line for t in report.tests)


def test_generated_tests_run_next_to_the_project_suite(
    builder: RunnerImageBuilder, sandbox: DockerSandbox
) -> None:
    runner = TestSuiteRunner(sandbox, builder)  # fresh image: exercises the real build path
    root = EXAMPLES / "buggy_inventory"
    generated = {
        "testmind/test_inventory_stock.py": (
            "from inventory.stock import average_price\n\n"
            "def test_average_of_zero_items():\n    assert average_price(10, 0) == 0\n"
        )
    }
    report = runner.run(root, RepositoryAnalyzer().analyze(root), generated)

    assert report.outcome is ExecutionOutcome.FAILED
    assert report.install is not None
    assert report.install.requirements == ["pyyaml>=6.0", "requests>=2.31"]
    [failure] = report.failures()
    assert failure.generated is True
    assert failure.nodeid == "tests/testmind/test_inventory_stock.py::test_average_of_zero_items"
    assert failure.message is not None
    assert "ZeroDivisionError" in failure.message
    assert failure.traceback is not None


@pytest.mark.parametrize(
    "job",
    [
        PytestJob(project_tests=("../escape.py",)),
        PytestJob(test_files={"t.py": ""}, env={"lower": "x"}),
        PytestJob(test_files={"t.py": ""}, env={"OK": "has space"}),
    ],
)
def test_invalid_jobs_are_refused(sandbox: DockerSandbox, job: PytestJob) -> None:
    with pytest.raises(ValueError, match="Invalid"):
        sandbox.run(EXAMPLES / "discount_service", job)
