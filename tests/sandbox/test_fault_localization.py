"""Fault localisation for real: a project whose bug only makes the tests' assertions fail, so
the traceback holds no production frame, run in the sandbox with per-test coverage."""

from __future__ import annotations

from pathlib import Path

from testmind.failure_engine import FailureCategory
from testmind.fault_localization import Confidence, Spectrum, localize
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.models import Outcome
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.runner import TestSuiteRunner
from tests.sandbox.conftest import SANDBOX_IMAGE
from tests.sandbox.test_suite_runner import analyse
from tests.unit.analyzers.helpers import make_repo

PRICING = """\
def apply_discount(price, percent):
    return price + price * percent / 100  # the bug: + instead of -


def tax(price):
    return price * 1.2


def total(price, percent):
    return tax(apply_discount(price, percent))


def label(value):
    return f"{value:.2f} EUR"
"""
TESTS = """\
from shop.pricing import apply_discount, label, tax, total


def test_tax():
    assert tax(100) == 120


def test_label():
    assert label(3) == "3.00 EUR"


def test_total_without_discount():
    assert total(100, 0) == 120


def test_discount():
    assert apply_discount(100, 10) == 90


def test_total_with_discount():
    assert total(100, 10) == 108
"""


def test_an_assertion_only_bug_is_localised(sandbox: DockerSandbox, tmp_path: Path) -> None:
    root = make_repo(
        tmp_path / "repo",
        {"shop/__init__.py": "", "shop/pricing.py": PRICING, "tests/test_pricing.py": TESTS},
    )
    repository, code = analyse(root)
    runner = TestSuiteRunner(sandbox, RunnerImageBuilder(SANDBOX_IMAGE))

    result = runner.run(root, repository, code=code, test_contexts=True)

    failed = {t.name for t in result.report.tests if t.outcome is Outcome.FAILED}
    assert failed == {"test_discount", "test_total_with_discount"}
    assert result.spectrum is not None
    assert result.failures is not None
    by_test = {g.tests[0].name: g for g in result.failures.groups}
    discount = by_test["test_discount"]
    assert discount.category is FailureCategory.ASSERTION_FAILURE
    assert discount.function is None  # the traceback alone blames nothing
    assert discount.localization is not None
    [top, *_] = discount.localization.suspects
    assert (top.qualname, top.rank, top.in_traceback) == ("apply_discount", 1, False)
    assert discount.localization.confidence is Confidence.HIGH
    # The other failure executed total, tax and apply_discount: total and apply_discount tie.
    other = by_test["test_total_with_discount"].localization
    assert other is not None
    assert {s.qualname for s in other.suspects if s.rank == 2} == {"apply_discount", "total"}
    assert other.confidence is Confidence.MEDIUM

    # Both failing tests together: only apply_discount explains both, first by a margin.
    passing = {t.nodeid for t in result.report.tests if t.outcome is Outcome.PASSED}
    failing = {t.nodeid for t in result.report.tests if t.outcome is Outcome.FAILED}
    both = localize(Spectrum.from_json(result.spectrum.to_json()), failing, passing)
    assert [(s.qualname, s.rank) for s in both.suspects[:2]] == [
        ("apply_discount", 1),
        ("total", 2),
    ]
    assert "label" not in {s.qualname for s in both.suspects}  # no failing test executed it
