"""Analysis of a pull request: base and head, downloaded from GitHub, compared deterministically.

1. Regression analysis between the two commits (all tests at both, with the selection check).
2. Code-analysis findings in the changed files that the base does not have. Findings are
   matched by rule, file, enclosing symbol and the offending code, not by line number, so
   code that only moved is not "new".
3. Added or modified functions that no test executes at head.
4. The conclusion for the check run, and issue suggestions (created only on request).

Everything here is deterministic; the markdown posted to GitHub is rendered from the report.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.code.models import Finding, Severity
from testmind.analyzers.repository import RepositoryAnalyzer
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.github.client import PullRequest
from testmind.github.models import Conclusion, IssueSuggestion, PullReport
from testmind.regression.engine import RegressionEngine
from testmind.regression.models import (
    RegressionMode,
    RegressionReport,
    RunSummary,
    TestChangeKind,
    Verdict,
)
from testmind.vcs import CommitInfo, Snapshot
from testmind.vcs.snapshots import SnapshotSource

SERIOUS = frozenset({Severity.HIGH, Severity.CRITICAL})
MARKER = "<!-- testmind:pull-analysis -->"
LABELS = ["testmind"]
MAX_ROWS = 20
T = TypeVar("T")


class PullAnalyzer:
    def __init__(self, engine: RegressionEngine, limits: AnalysisLimits | None = None) -> None:
        self._engine = engine
        self._limits = limits or AnalysisLimits()

    def analyze(self, pull: PullRequest, base: Snapshot, head: Snapshot) -> PullReport:
        source = SnapshotSource({pull.base_sha: base, pull.head_sha: head}, self._limits)
        regression = self._engine.compare(
            source,
            CommitInfo(sha=pull.base_sha, ref=pull.base_ref, subject="", author="", timestamp=0),
            CommitInfo(
                sha=pull.head_sha, ref=pull.head_ref, subject=pull.title, author="", timestamp=0
            ),
            RegressionMode.FULL,
        )
        changed = {f.path for f in regression.changes.files if f.status != "deleted"}
        new_findings = new_findings_in(self._code(base.root), self._code(head.root), changed)
        untested = [
            f
            for f in regression.changes.functions
            if f.status in {"added", "modified"} and f.head_coverage == 0.0
        ]
        report = PullReport(
            repository=pull.repository,
            number=pull.number,
            title=pull.title,
            url=pull.html_url,
            conclusion=conclusion(regression, new_findings),
            regression=regression,
            new_findings=new_findings,
            untested_changes=untested,
            suggestions=[],
            notes=[],
        )
        return report.model_copy(update={"suggestions": suggestions(report)})

    def _code(self, root: Path) -> CodeProfile:
        repository = RepositoryAnalyzer(self._limits).analyze(root)
        python = [f.path for f in repository.files if f.language == "Python"]
        return CodeAnalyzer(self._limits).analyze(root, python, repository.test_files)


def new_findings_in(base: CodeProfile, head: CodeProfile, changed: set[str]) -> list[Finding]:
    """Findings at head, in changed files, beyond those the base already had."""
    remaining = Counter(_key(f) for f in base.findings)
    new = []
    for finding in head.findings:
        key = _key(finding)
        if remaining[key]:
            remaining[key] -= 1
        elif finding.path in changed:
            new.append(finding)
    return new


def _key(finding: Finding) -> tuple[str, str, str | None, str]:
    return finding.rule_id, finding.path, finding.symbol, finding.evidence.strip()


def conclusion(regression: RegressionReport, findings: list[Finding]) -> Conclusion:
    if regression.verdict is Verdict.REGRESSIONS or any(f.severity in SERIOUS for f in findings):
        return Conclusion.FAILURE
    if regression.verdict is Verdict.INCONCLUSIVE:
        return Conclusion.NEUTRAL
    return Conclusion.SUCCESS


# --------------------------------------------------------------------------- suggestions
def suggestions(report: PullReport) -> list[IssueSuggestion]:
    found: list[IssueSuggestion] = []
    reference = f"{report.repository}#{report.number}"
    for test in _regressed(report.regression):
        found.append(
            _suggestion(
                "regression",
                test,
                f"Regression: {test} fails after {reference}",
                f"`{test}` passed at `{_short(report.regression.base.sha)}` and fails at "
                f"`{_short(report.regression.head.sha)}` ({report.url}).",
            )
        )
    for finding in report.new_findings:
        if finding.severity in SERIOUS:
            found.append(
                _suggestion(
                    "finding",
                    f"{finding.rule_id}:{finding.path}:{finding.symbol}:{finding.evidence}",
                    f"[{finding.rule_id}] {finding.message} in {finding.path}",
                    f"{finding.severity.value.capitalize()} finding introduced by {report.url}, "
                    f"at `{finding.path}:{finding.line}`:\n\n```python\n{finding.evidence}\n```",
                )
            )
    if report.untested_changes:
        names = "\n".join(
            f"- `{f.path}::{f.qualname}` ({f.status})" for f in report.untested_changes
        )
        found.append(
            _suggestion(
                "untested_changes",
                ",".join(f"{f.path}::{f.qualname}" for f in report.untested_changes),
                f"Add tests for {len(report.untested_changes)} function(s) changed in {reference}",
                f"No test executes these functions after {report.url}:\n\n{names}",
            )
        )
    return found


def _suggestion(kind: str, key: str, title: str, body: str) -> IssueSuggestion:
    digest = hashlib.sha256(f"{kind}\x1f{key}".encode()).hexdigest()[:12]
    return IssueSuggestion(
        id=digest, kind=kind, title=title[:250], body=body + "\n\n_Suggested by TestMind._",
        labels=LABELS,
    )  # fmt: skip


def _regressed(regression: RegressionReport) -> list[str]:
    return [t.nodeid for t in regression.tests if t.change is TestChangeKind.REGRESSED]


# ------------------------------------------------------------------------------ markdown
def title(report: PullReport) -> str:
    regression = report.regression
    parts = []
    regressed = len(_regressed(regression))
    if regressed:
        parts.append(f"{regressed} regressed test(s)")
    serious = sum(1 for f in report.new_findings if f.severity in SERIOUS)
    if serious:
        parts.append(f"{serious} new high or critical finding(s)")
    if not parts and regression.verdict is Verdict.INCONCLUSIVE:
        parts.append("the tests could not run")
    return ", ".join(parts) if parts else "No regressions"


def render(report: PullReport, *, analysis_url: str | None = None) -> str:
    """The comment posted on the pull request (also the check run's summary)."""
    regression = report.regression
    lines = [
        MARKER,
        f"## TestMind: {title(report)}",
        "",
        f"**Conclusion: {report.conclusion.value}.** Head `{_short(regression.head.sha)}` "
        f"compared with `{regression.base.ref}` (`{_short(regression.base.sha)}`); "
        "the whole test suite ran at both commits.",
        "",
        "| | Base | Head |",
        "|---|---|---|",
        f"| Tests | {_totals(regression.base_run)} | {_totals(regression.head_run)} |",
        f"| Coverage | {_percent(regression.base_run)} | {_percent(regression.head_run)} |",
    ]
    lines += _section(
        "Regressions",
        _regressed(regression),
        lambda test: f"- `{test}` passed at base, fails at head",
    )
    lines += _section(
        "New failure causes",
        regression.failures.new,
        lambda g: (
            f"- {g.category}: `{g.exception_type or 'unknown'}` at `{g.crash or '?'}` "
            f"({g.tests} test(s))"
        ),
    )
    fixed = [t.nodeid for t in regression.tests if t.change is TestChangeKind.FIXED]
    lines += _section("Fixed", fixed, lambda test: f"- `{test}`")
    lines += _section(
        "New findings in changed files",
        report.new_findings,
        lambda f: f"- **{f.severity.value}** `{f.rule_id}` `{f.path}:{f.line}`: {f.message}",
    )
    lines += _section(
        "Changed functions that no test executes",
        report.untested_changes,
        lambda f: f"- `{f.path}::{f.qualname}` ({f.status})",
    )
    check = regression.selection_check
    if check is not None and check.regressed:
        missed = ", ".join(f"`{m}`" for m in check.missed) or "none"
        lines += ["", f"Test selection check: {check.regressed} regression(s), missed by the "
                  f"deterministic selection: {missed}."]  # fmt: skip
    if report.suggestions:
        where = f" ({analysis_url})" if analysis_url else ""
        lines += ["", f"<details><summary>{len(report.suggestions)} suggested issue(s){where}"
                  "</summary>", ""]  # fmt: skip
        lines += [f"- `{s.id}` {s.title}" for s in report.suggestions]
        lines += ["", "</details>"]
    notes = regression.notes + regression.base_run.snapshot_notes[:5] + report.notes
    lines += _section("Notes", notes, lambda n: f"- {n}")
    return "\n".join(lines) + "\n"


def _section(heading: str, items: Iterable[T], row: Callable[[T], str]) -> list[str]:
    listed = list(items)
    if not listed:
        return []
    shown = [row(item) for item in listed[:MAX_ROWS]]
    more = [f"- … and {len(listed) - MAX_ROWS} more"] if len(listed) > MAX_ROWS else []
    return ["", f"### {heading} ({len(listed)})", "", *shown, *more]


def _totals(run: RunSummary) -> str:
    totals = run.totals
    if not totals.total:
        return run.outcome.value.replace("_", " ")
    failed = totals.failed + totals.errors
    return f"{totals.passed} passed, {failed} failed" + (
        f", {totals.skipped} skipped" if totals.skipped else ""
    )


def _percent(run: RunSummary) -> str:
    return f"{run.coverage_percent:.1f} %" if run.coverage_percent is not None else "n/a"


def _short(sha: str) -> str:
    return sha[:7]
