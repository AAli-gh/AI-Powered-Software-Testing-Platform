"""The pull request report: new findings, the conclusion, issue suggestions and the markdown.
The whole analysis runs in the real sandbox in tests/sandbox/test_github.py."""

from typing import Any

from testmind.analyzers.code.models import (
    CodeMetrics,
    CodeProfile,
    Finding,
    FindingCategory,
    Severity,
)
from testmind.github.models import Conclusion, PullReport
from testmind.github.pull_analysis import (
    MARKER,
    conclusion,
    new_findings_in,
    render,
    suggestions,
    title,
)
from testmind.regression.models import ChangedFunction, RegressionReport


def finding(
    rule: str, path: str, line: int, severity: Severity = Severity.HIGH, evidence: str = "x"
) -> Finding:
    return Finding(
        rule_id=rule, category=FindingCategory.DANGEROUS_PATTERN, severity=severity,
        confidence=1.0, path=path, line=line, symbol="f", message=f"{rule} message",
        evidence=evidence,
    )  # fmt: skip


def profile(*findings: Finding) -> CodeProfile:
    metrics = CodeMetrics(
        modules=0, test_modules_skipped=0, lines=0, functions=0, methods=0, classes=0,
        average_complexity=0, max_complexity=0, complexity_ranks={}, findings_by_severity={},
        findings_by_category={},
    )  # fmt: skip
    return CodeProfile(
        metrics=metrics, issues=[], modules=[], functions=[], classes=[], findings=list(findings)
    )


def test_new_findings_are_matched_without_line_numbers() -> None:
    base = profile(finding("TM010", "a.py", 3), finding("TM020", "b.py", 1))
    head = profile(
        finding("TM010", "a.py", 9),  # moved: not new
        finding("TM010", "a.py", 12),  # a second one: new
        finding("TM030", "c.py", 1),  # in a file the pull request does not change: not reported
        finding("TM040", "a.py", 2, evidence="y"),
    )
    new = new_findings_in(base, head, changed={"a.py"})
    assert [(f.rule_id, f.line) for f in new] == [("TM010", 12), ("TM040", 2)]


def run(outcome: str = "passed", passed: int = 1, failed: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "commit": "c" * 40, "outcome": outcome,
        "totals": {"total": passed + failed + extra.get("skipped", 0), "passed": passed,
                   "failed": failed, "skipped": extra.get("skipped", 0)},
        "coverage_percent": extra.get("coverage"), "failure_groups": None,
        "selected_only": False, "snapshot_notes": extra.get("notes", []),
    }  # fmt: skip


def regression(verdict: str = "clean", **changes: Any) -> RegressionReport:
    document: dict[str, Any] = {
        "base": {"sha": "a" * 40, "ref": "main", "subject": "", "author": "", "timestamp": 0},
        "head": {"sha": "b" * 40, "ref": "feature", "subject": "", "author": "", "timestamp": 0},
        "mode": "full", "verdict": verdict,
        "changes": {"files": [], "functions": [], "module_level": [], "notes": []},
        "selection": {"everything": False, "targets": [], "notes": []},
        "base_run": run(coverage=80.0), "head_run": run(coverage=75.5),
        "tests": [], "failures": {"new": [], "resolved": [], "persisting": []},
        "coverage": None, "selection_check": None, "notes": [],
    }  # fmt: skip
    document.update(changes)
    return RegressionReport.model_validate(document)


def report(regression_report: RegressionReport, **changes: Any) -> PullReport:
    values: dict[str, Any] = {
        "repository": "acme/shop", "number": 7, "title": "Change", "url": "https://gh/pr/7",
        "conclusion": Conclusion.SUCCESS, "regression": regression_report, "new_findings": [],
        "untested_changes": [], "suggestions": [], "notes": [],
    }  # fmt: skip
    values.update(changes)
    return PullReport(**values)


def test_conclusions() -> None:
    assert conclusion(regression(), []) is Conclusion.SUCCESS
    assert (
        conclusion(regression(), [finding("TM1", "a.py", 1, Severity.MEDIUM)]) is Conclusion.SUCCESS
    )
    assert (
        conclusion(regression(), [finding("TM1", "a.py", 1, Severity.CRITICAL)])
        is Conclusion.FAILURE
    )
    assert conclusion(regression("regressions"), []) is Conclusion.FAILURE
    assert conclusion(regression("inconclusive"), []) is Conclusion.NEUTRAL


def test_a_clean_pull_request() -> None:
    clean = report(regression())
    assert title(clean) == "No regressions"
    assert suggestions(clean) == []
    assert render(clean) == (
        f"{MARKER}\n## TestMind: No regressions\n\n**Conclusion: success.** Head `bbbbbbb` "
        "compared with `main` (`aaaaaaa`); the whole test suite ran at both commits.\n\n"
        "| | Base | Head |\n|---|---|---|\n| Tests | 1 passed, 0 failed | 1 passed, 0 failed |\n"
        "| Coverage | 80.0 % | 75.5 % |\n"
    )


def test_an_inconclusive_run_and_long_lists() -> None:
    tests = [
        {"nodeid": f"t.py::test_{i}", "base": "passed", "head": "failed", "change": "regressed",
         "selected": True}
        for i in range(25)
    ] + [{"nodeid": "t.py::test_fixed", "base": "failed", "head": "passed", "change": "fixed",
          "selected": True}]  # fmt: skip
    failing = regression(
        "regressions",
        tests=tests,
        head_run=run("sandbox_unavailable", passed=0),
        base_run=run(passed=3, failed=1, skipped=2, notes=["x.py: symbolic link"]),
        selection_check={"regressed": 25, "missed": ["t.py::test_3"], "selected_tests": 26,
                         "total_tests": 26},
        failures={"new": [{"id": "g", "category": "assertion_failure", "exception_type": None,
                           "crash": None, "tests": 25}], "resolved": [], "persisting": []},
        notes=["something to know"],
    )  # fmt: skip
    untested = [
        ChangedFunction(path="a.py", qualname="f", status="added", base_lines=None,
                        head_lines=(1, 2), head_coverage=0.0)
    ]  # fmt: skip
    full = report(
        failing, conclusion=Conclusion.FAILURE, untested_changes=untested,
        new_findings=[finding("TM010", "a.py", 2, Severity.CRITICAL),
                      finding("TM5", "a.py", 3, Severity.LOW)],
    )  # fmt: skip
    full = full.model_copy(update={"suggestions": suggestions(full)})
    assert [s.kind for s in full.suggestions] == ["regression"] * 25 + [
        "finding",
        "untested_changes",
    ]
    assert len({s.id for s in full.suggestions}) == 27  # stable, distinct ids
    assert suggestions(full)[0].id == full.suggestions[0].id
    assert title(full) == "25 regressed test(s), 1 new high or critical finding(s)"
    markdown = render(full, analysis_url="https://testmind/x")
    assert "| Tests | 3 passed, 1 failed, 2 skipped | sandbox unavailable |" in markdown
    assert "| Coverage | n/a | n/a |" in markdown
    assert "### Regressions (25)" in markdown
    assert "- … and 5 more" in markdown
    assert "- assertion_failure: `unknown` at `?` (25 test(s))" in markdown
    assert "### Fixed (1)" in markdown
    assert "- **critical** `TM010` `a.py:2`: TM010 message" in markdown
    assert "- `a.py::f` (added)" in markdown
    assert "missed by the deterministic selection: `t.py::test_3`." in markdown
    assert "27 suggested issue(s) (https://testmind/x)" in markdown
    assert "### Notes (2)\n\n- something to know\n- x.py: symbolic link" in markdown
    no_coverage = report(regression("inconclusive", head_run=run("timed_out", passed=0)))
    assert title(no_coverage) == "the tests could not run"
    assert "| Coverage | 80.0 % | n/a |" in render(no_coverage)


def test_coverage_not_measured() -> None:
    unmeasured = report(regression(base_run=run(coverage=None), head_run=run(coverage=None)))
    assert "| Coverage | n/a | n/a |" in render(unmeasured)
