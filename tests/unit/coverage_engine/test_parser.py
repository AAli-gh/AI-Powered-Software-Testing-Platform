import json

import pytest

from testmind.coverage_engine import CoverageParseError, parse_coverage_json


def report(files: dict[str, object], meta: dict[str, object] | None = None) -> str:
    return json.dumps(
        {
            "meta": {"version": "7.16.1", "branch_coverage": True} if meta is None else meta,
            "files": files,
        }
    )


FILE = {
    "executed_lines": [1, 2],
    "missing_lines": [3],
    "executed_branches": [[2, 3]],
    "missing_branches": [[2, -1]],
    "summary": {"ignored": True},
}


def test_paths_relative_to_the_repository_root() -> None:
    raw = parse_coverage_json(report({"pkg/mod.py": FILE}), rootdir="/workspace/src")
    data = raw.files["pkg/mod.py"]
    assert data.executed == {1, 2}
    assert data.missing == {3}
    assert data.statements == {1, 2, 3}
    assert data.executed_branches == {(2, 3)}
    assert data.missing_branches == {(2, -1)}
    assert (raw.tool, raw.branch_coverage) == ("coverage.py 7.16.1", True)


def test_absolute_paths_when_pytest_ran_elsewhere() -> None:
    """Without project tests pytest runs in /workspace/tests; the repository is then reported
    with absolute paths, and anything outside it is dropped."""
    raw = parse_coverage_json(
        report(
            {
                "/workspace/src/app.py": FILE,
                "testmind/test_x.py": FILE,  # the generated tests themselves
                "/usr/lib/python3.12/site.py": FILE,
                "../src/other.py": FILE,
            }
        ),
        rootdir="/workspace/tests",
    )
    assert sorted(raw.files) == ["app.py", "other.py"]


def test_escaping_paths_are_dropped() -> None:
    raw = parse_coverage_json(
        report({"../../etc/passwd": FILE, "/workspace/srcx/a.py": FILE}),
        rootdir="/workspace/src",
    )
    assert raw.files == {}


def test_line_coverage_only_and_unknown_version() -> None:
    raw = parse_coverage_json(
        report({"a.py": {"executed_lines": [1], "missing_lines": []}}, meta={}),
        rootdir="/workspace/src",
    )
    assert (raw.tool, raw.branch_coverage) == ("coverage.py", False)
    assert raw.files["a.py"].executed_branches == frozenset()


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"meta": {}}),
        json.dumps({"files": {"a.py": {"executed_lines": ["x"], "missing_lines": []}}}),
        json.dumps({"files": {"a.py": {"missing_lines": []}}}),
    ],
)
def test_invalid_reports_are_rejected(text: str) -> None:
    with pytest.raises(CoverageParseError, match=r"Not a coverage\.py JSON report"):
        parse_coverage_json(text, rootdir="/workspace/src")
