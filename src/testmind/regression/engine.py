"""Regression analysis between two commits of a repository.

1. Resolve both revisions and list the changed files (git objects, read with dulwich).
2. Write each commit into its own temporary directory; the repository itself is never
   touched. Analyse both (repository + code analysis).
3. Run the suite at base, with per-test coverage contexts.
4. Diff the changed Python modules at function level and select the affected tests.
5. Run the suite at head: everything (full mode, which also checks the selection) or only
   the selection.
6. Compare test outcomes, failure causes and coverage.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryAnalyzer, RepositoryProfile
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.coverage_engine import CoverageReport, compare_coverage
from testmind.regression.compare import check_selection, compare_failures, compare_tests
from testmind.regression.diff import ModuleDiff, diff_module
from testmind.regression.models import (
    ChangedFunction,
    Changes,
    Commit,
    RegressionMode,
    RegressionReport,
    RunSummary,
    Selection,
    TestChange,
    TestChangeKind,
    Verdict,
)
from testmind.regression.selection import classify, pytest_targets, select_tests
from testmind.test_engine.models import ExecutionOutcome, TestTotals
from testmind.test_engine.runner import SuiteResult, TestSuiteRunner
from testmind.vcs import CommitInfo, GitRepository, Snapshot

UNRUNNABLE = frozenset(
    {
        ExecutionOutcome.SANDBOX_UNAVAILABLE,
        ExecutionOutcome.INSTALL_FAILED,
        ExecutionOutcome.TIMED_OUT,
    }
)
NOTHING_SELECTED = "No test is affected by the change; nothing was run at head"


class RegressionEngine:
    def __init__(
        self,
        runner: TestSuiteRunner,
        *,
        limits: AnalysisLimits | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._runner = runner
        self._limits = limits or AnalysisLimits()
        self._on_progress = on_progress or (lambda _: None)

    def run(
        self, repo_root: Path, base_ref: str, head_ref: str, mode: RegressionMode
    ) -> RegressionReport:
        git = GitRepository(repo_root, self._limits)
        try:
            base, head = git.resolve(base_ref), git.resolve(head_ref)
            with tempfile.TemporaryDirectory(prefix="testmind-regression-") as scratch:
                return self._compare(git, base, head, mode, Path(scratch))
        finally:
            git.close()

    def _compare(
        self,
        git: GitRepository,
        base: CommitInfo,
        head: CommitInfo,
        mode: RegressionMode,
        scratch: Path,
    ) -> RegressionReport:
        self._on_progress("snapshots")
        base_snapshot = _materialize(git, base.sha, scratch / "base")
        head_snapshot = _materialize(git, head.sha, scratch / "head")
        base_profile, base_code = self._analyse(base_snapshot.root)
        head_profile, head_code = self._analyse(head_snapshot.root)

        self._on_progress("base")
        base_run = self._runner.run(
            base_snapshot.root, base_profile, code=base_code, test_contexts=True
        )

        files = [classify(c, head_profile, base_profile) for c in git.changes(base.sha, head.sha)]
        diffs: dict[str, ModuleDiff] = {
            f.path: diff_module(f.path, git.read(base.sha, f.path), git.read(head.sha, f.path))
            for f in files
            if f.kind == "production" and f.path.endswith(".py")
        }
        selection = select_tests(
            files,
            diffs,
            head=head_profile,
            head_code=head_code,
            read_head=lambda path: git.read(head.sha, path),
            base_impact=base_run.impact,
        )

        self._on_progress("head")
        head_run = self._head_run(head_snapshot.root, head_profile, head_code, selection, mode)

        head_tests = set(head_profile.test_files)
        tests = compare_tests(
            base_run.report.tests,
            head_run.report.tests if head_run else [],
            selection=selection,
            mode=mode,
            head_test_files=head_tests,
        )
        full = mode is RegressionMode.FULL
        notes = [NOTHING_SELECTED] if head_run is None else []
        return RegressionReport(
            base=_commit(base),
            head=_commit(head),
            mode=mode,
            verdict=_verdict(base_run, head_run, tests),
            changes=Changes(
                files=files,
                functions=_functions(diffs, head_run.coverage if head_run else None),
                module_level=sorted(p for p, d in diffs.items() if d.module_level_changed),
                notes=[n for d in diffs.values() if (n := d.parse_error)],
            ),
            selection=selection,
            base_run=_summary(base_run, base.sha, base_snapshot, selected=False),
            head_run=_summary(head_run, head.sha, head_snapshot, selected=not full),
            tests=tests,
            failures=compare_failures(base_run.failures, head_run.failures if head_run else None),
            coverage=(
                compare_coverage(base_run.coverage, head_run.coverage)
                if full and head_run and base_run.coverage and head_run.coverage
                else None
            ),
            selection_check=(
                check_selection(tests, len(head_run.report.tests)) if full and head_run else None
            ),
            notes=notes,
        )

    def _head_run(
        self,
        root: Path,
        profile: RepositoryProfile,
        code: CodeProfile,
        selection: Selection,
        mode: RegressionMode,
    ) -> SuiteResult | None:
        if mode is RegressionMode.FULL or selection.everything:
            return self._runner.run(root, profile, code=code)
        if not selection.targets:
            return None
        return self._runner.run(root, profile, code=code, select=pytest_targets(selection))

    def _analyse(self, root: Path) -> tuple[RepositoryProfile, CodeProfile]:
        repository = RepositoryAnalyzer(self._limits).analyze(root)
        python = [
            f.path for f in repository.files if f.language == "Python" and f.path.endswith(".py")
        ]
        code = CodeAnalyzer(self._limits).analyze(root, python, repository.test_files)
        return repository, code


def _materialize(git: GitRepository, sha: str, destination: Path) -> Snapshot:
    destination.mkdir()
    return git.materialize(sha, destination)


def _commit(info: CommitInfo) -> Commit:
    return Commit(
        sha=info.sha,
        ref=info.ref,
        subject=info.subject,
        author=info.author,
        timestamp=info.timestamp,
    )


def _functions(
    diffs: dict[str, ModuleDiff], coverage: CoverageReport | None
) -> list[ChangedFunction]:
    percent = {(f.path, f.qualname): f.percent for f in (coverage.functions if coverage else [])}
    return [
        ChangedFunction(
            path=change.path,
            qualname=change.qualname,
            status=change.status.value,
            base_lines=change.base_lines,
            head_lines=change.head_lines,
            head_coverage=percent.get((change.path, change.qualname)),
        )
        for diff in diffs.values()
        for change in diff.functions
    ]


def _summary(
    run: SuiteResult | None, sha: str, snapshot: Snapshot, *, selected: bool
) -> RunSummary:
    notes = [f"not written: {s}" for s in snapshot.skipped]
    if run is None:
        return RunSummary(
            commit=sha, outcome=ExecutionOutcome.NO_TESTS, totals=TestTotals(),
            coverage_percent=None, failure_groups=None, selected_only=selected,
            snapshot_notes=notes,
        )  # fmt: skip
    return RunSummary(
        commit=sha,
        outcome=run.report.outcome,
        totals=run.report.totals,
        coverage_percent=run.coverage.totals.percent if run.coverage else None,
        failure_groups=len(run.failures.groups) if run.failures else None,
        selected_only=selected,
        snapshot_notes=notes,
    )


def _verdict(base: SuiteResult, head: SuiteResult | None, tests: list[TestChange]) -> Verdict:
    if base.report.outcome in UNRUNNABLE or (head and head.report.outcome in UNRUNNABLE):
        return Verdict.INCONCLUSIVE
    if any(t.change is TestChangeKind.REGRESSED for t in tests):
        return Verdict.REGRESSIONS
    return Verdict.CLEAN
