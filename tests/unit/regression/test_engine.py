"""Engine outcomes that need no real sandbox (canned sandbox; git, diff and selection real)."""

from pathlib import Path

import pytest

from testmind.regression.engine import RegressionEngine
from testmind.regression.models import RegressionMode, Verdict
from testmind.test_engine.models import ExecutionOutcome
from testmind.test_engine.runner import TestSuiteRunner
from testmind.vcs import GitError
from tests.regression_fixture import make_shop_repo
from tests.unit.test_engine.test_runner import Images, Sandbox


def test_no_sandbox_is_inconclusive(tmp_path: Path) -> None:
    make_shop_repo(tmp_path / "shop")
    engine = RegressionEngine(TestSuiteRunner(Sandbox(available=False), Images()))
    report = engine.run(tmp_path / "shop", "v1", "HEAD", RegressionMode.FULL)
    assert report.verdict is Verdict.INCONCLUSIVE
    assert report.base_run.outcome is ExecutionOutcome.SANDBOX_UNAVAILABLE
    # Without per-test coverage the selection falls back to imports, and says so.
    assert "No per-test coverage at base" in report.selection.notes[-1]
    assert report.coverage is None


def test_unknown_revisions_fail(tmp_path: Path) -> None:
    make_shop_repo(tmp_path / "shop")
    engine = RegressionEngine(TestSuiteRunner(Sandbox(available=False), Images()))
    with pytest.raises(GitError, match="Unknown revision"):
        engine.run(tmp_path / "shop", "nope", "HEAD", RegressionMode.FULL)
