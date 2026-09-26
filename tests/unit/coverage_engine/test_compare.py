from testmind.coverage_engine import CoverageReport, compare_coverage
from testmind.coverage_engine.models import CoverageTotals, FileCoverage, FunctionCoverage
from testmind.coverage_engine.report import percent


def file(path: str, statements: int, missing: list[int]) -> FileCoverage:
    covered = statements - len(missing)
    value = percent(covered, statements)
    return FileCoverage(
        path=path,
        statements=statements,
        covered_lines=covered,
        branches=0,
        covered_branches=0,
        line_percent=value,
        branch_percent=100.0,
        percent=value,
        missing_lines=missing,
        missing=[str(m) for m in missing],
        missing_branches_detail=[],
    )


def function(path: str, qualname: str, value: float) -> FunctionCoverage:
    return FunctionCoverage(
        path=path,
        qualname=qualname,
        line=1,
        end_line=2,
        statements=1,
        covered_lines=1 if value else 0,
        branches=0,
        covered_branches=0,
        line_percent=value,
        branch_percent=100.0,
        percent=value,
        missing=[],
        missing_branches_detail=[],
        complexity=1,
        findings=[],
        is_public=True,
    )


def report(files: list[FileCoverage], functions: list[FunctionCoverage]) -> CoverageReport:
    statements = sum(f.statements for f in files)
    covered = sum(f.covered_lines for f in files)
    value = percent(covered, statements)
    return CoverageReport(
        tool="coverage.py",
        branch_coverage=True,
        threshold=80,
        totals=CoverageTotals(
            statements=statements,
            covered_lines=covered,
            branches=0,
            covered_branches=0,
            line_percent=value,
            branch_percent=100.0,
            percent=value,
            files=len(files),
            functions=len(functions),
            functions_never_executed=0,
        ),
        files=files,
        functions=functions,
        gaps=[],
        unmeasured_files=[],
    )


def test_generated_tests_raise_coverage() -> None:
    base = report(
        [file("a.py", 10, [3, 4, 5, 6]), file("b.py", 4, []), file("gone.py", 2, [1])],
        [function("a.py", "f", 0.0), function("b.py", "g", 100.0), function("gone.py", "h", 0.0)],
    )
    head = report(
        [file("a.py", 10, [6, 9]), file("b.py", 4, []), file("new.py", 2, [])],
        [
            function("a.py", "f", 100.0),
            function("b.py", "g", 100.0),
            function("new.py", "k", 100.0),
        ],
    )
    diff = compare_coverage(base, head)

    assert (diff.base_percent, diff.head_percent) == (base.totals.percent, head.totals.percent)
    assert diff.delta == round(head.totals.percent - base.totals.percent, 2)
    assert (diff.newly_covered_lines, diff.newly_missed_lines) == (3, 1)  # 3,4,5 now run; 9 not
    assert [(f.path, f.delta, f.newly_covered_lines) for f in diff.files] == [
        ("a.py", 20.0, 3),
        ("gone.py", None, 0),  # only in one report: no delta
        ("new.py", None, 0),
    ]
    assert [(f.qualname, f.base_percent, f.head_percent, f.delta) for f in diff.functions] == [
        ("f", 0.0, 100.0, 100.0),
        ("h", 0.0, None, None),
        ("k", None, 100.0, None),
    ]


def test_identical_reports_have_no_changes() -> None:
    same = report([file("a.py", 10, [3])], [function("a.py", "f", 90.0)])
    diff = compare_coverage(same, same)
    assert (diff.delta, diff.files, diff.functions) == (0.0, [], [])
