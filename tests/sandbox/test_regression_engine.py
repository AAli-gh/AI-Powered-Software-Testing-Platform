"""The regression engine on a real git history (tests/regression_fixture.py), in the real
sandbox: per-test coverage at base, function-level diff, selection, both runs, comparison."""

from pathlib import Path

import pytest

from testmind.regression.engine import RegressionEngine
from testmind.regression.models import RegressionMode, RegressionReport, TestChangeKind, Verdict
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner
from tests.gitrepo import make_git_repo
from tests.regression_fixture import make_shop_repo

EXPECTED_TARGETS = {
    "tests/test_cart.py::TestCart::test_total": ["executed discount (modified) at base"],
    "tests/test_pricing.py::test_discount": ["executed discount (modified) at base"],
    "tests/test_cart.py": ["imports shop.pricing (changed)"],
    "tests/test_pricing.py": ["imports shop.pricing (changed)"],
}


@pytest.fixture(scope="module")
def reports(
    sandbox: DockerSandbox, tmp_path_factory: pytest.TempPathFactory
) -> dict[RegressionMode, RegressionReport]:
    root = tmp_path_factory.mktemp("git") / "shop"
    make_shop_repo(root)
    engine = RegressionEngine(TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image)))
    return {mode: engine.run(root, "v1", "HEAD", mode) for mode in RegressionMode}


def test_changes_and_selection(reports: dict[RegressionMode, RegressionReport]) -> None:
    report = reports[RegressionMode.FULL]
    assert [(f.path, f.status, f.kind) for f in report.changes.files] == [
        ("shop/pricing.py", "modified", "production"),
        ("shop/unused.py", "modified", "production"),
    ]
    # `tax` only got a comment and a docstring: not a change.
    assert [(f.qualname, f.status) for f in report.changes.functions] == [
        ("discount", "modified"),
        ("rebate", "added"),
        ("helper", "modified"),
    ]
    # Coverage of the changed functions at head: the new `rebate` is untested.
    assert [f.head_coverage for f in report.changes.functions] == [100.0, 0.0, 0.0]
    assert {t.target: t.reasons for t in report.selection.targets} == EXPECTED_TARGETS
    assert report.selection.notes == [
        "shop/unused.py::helper (modified) was not executed by any test at base"
    ]


def test_full_mode_finds_the_regressions_and_checks_the_selection(
    reports: dict[RegressionMode, RegressionReport],
) -> None:
    report = reports[RegressionMode.FULL]
    assert report.verdict is Verdict.REGRESSIONS
    assert (report.base.ref, report.head.ref) == ("v1", "HEAD")
    assert report.base_run.outcome is ExecutionOutcome.PASSED
    assert (report.head_run.totals.passed, report.head_run.totals.failed) == (2, 2)
    changes = {t.nodeid: (t.change, t.selected) for t in report.tests}
    assert changes == {
        "tests/test_cart.py::test_total": (TestChangeKind.REGRESSED, True),  # via cart → discount
        "tests/test_pricing.py::test_discount": (TestChangeKind.REGRESSED, True),
        "tests/test_pricing.py::test_tax": (TestChangeKind.UNCHANGED, True),
        "tests/test_labels.py::test_label": (TestChangeKind.UNCHANGED, False),
    }
    check = report.selection_check
    assert check is not None
    assert (check.regressed, check.missed, check.selected_tests, check.total_tests) == (
        2, [], 3, 4,
    )  # fmt: skip
    assert sorted(g.crash or "" for g in report.failures.new) == [
        "tests/test_cart.py:6",
        "tests/test_pricing.py:5",
    ]
    assert report.coverage is not None
    assert report.coverage.base_percent > report.coverage.head_percent  # rebate is untested


def test_selected_mode_runs_only_the_selection(
    reports: dict[RegressionMode, RegressionReport],
) -> None:
    report = reports[RegressionMode.SELECTED]
    assert report.verdict is Verdict.REGRESSIONS
    assert report.head_run.selected_only is True
    assert report.head_run.totals.total == 3
    assert {t.nodeid: t.change for t in report.tests}["tests/test_labels.py::test_label"] is (
        TestChangeKind.NOT_RUN
    )
    assert (report.selection_check, report.coverage) == (None, None)


def test_nothing_affected_runs_nothing_at_head(sandbox: DockerSandbox, tmp_path: Path) -> None:

    root = tmp_path / "docs-only"
    make_git_repo(
        root,
        [
            (
                "code",
                {
                    "app.py": "def f():\n    return 1\n",
                    "tests/test_app.py": (
                        "from app import f\n\ndef test_f():\n    assert f() == 1\n"
                    ),
                },
            ),
            ("docs", {"README.md": "Docs only.\n"}),
        ],
    )
    engine = RegressionEngine(TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image)))
    report = engine.run(root, "HEAD~1", "HEAD", RegressionMode.SELECTED)
    assert report.verdict is Verdict.CLEAN
    assert [(f.path, f.kind) for f in report.changes.files] == [("README.md", "other")]
    assert report.selection.targets == []
    assert report.head_run.outcome is ExecutionOutcome.NO_TESTS
    assert report.notes == ["No test is affected by the change; nothing was run at head"]
    assert {t.nodeid: t.change for t in report.tests} == {
        "tests/test_app.py::test_f": TestChangeKind.NOT_RUN
    }
