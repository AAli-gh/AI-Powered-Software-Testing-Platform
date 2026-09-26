"""Shared fixtures: the golden failing_suite analysis (see test_analyzer.py)."""

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryAnalyzer, RepositoryProfile
from testmind.failure_engine import FailureAnalyzer, FailureReport, parse_failures_json
from tests.unit.failure_engine.test_analyzer import CAPTURE, EXAMPLE, failed_tests


@pytest.fixture(scope="module")
def profiles() -> tuple[RepositoryProfile, CodeProfile]:
    repository = RepositoryAnalyzer().analyze(EXAMPLE)
    python = [f.path for f in repository.files if f.language == "Python"]
    return repository, CodeAnalyzer().analyze(EXAMPLE, python, repository.test_files)


@pytest.fixture(scope="module")
def report(profiles: tuple[RepositoryProfile, CodeProfile]) -> FailureReport:
    repository, code = profiles
    capture = parse_failures_json(CAPTURE.read_text())
    return FailureAnalyzer(repository, code).analyze(capture, failed_tests())
