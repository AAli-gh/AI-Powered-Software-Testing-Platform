import re
from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import AnalysisLimits, RepositoryAnalyzer
from tests.unit.analyzers.helpers import make_repo

EXAMPLES = Path(__file__).resolve().parents[4] / "examples"


def analyze_repo(root: Path) -> CodeProfile:
    repository = RepositoryAnalyzer().analyze(root)
    python = [
        f.path for f in repository.files if f.language == "Python" and not f.path.endswith(".pyi")
    ]
    return CodeAnalyzer().analyze(root, python, repository.test_files)


def test_metrics(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        {
            "app/core.py": "def a():\n    if x:\n        pass\n\nclass K:\n    def m(self): pass\n",
            "app/util.py": "import os\n",
            "tests/test_core.py": "def test_a(): assert True\n",
        },
    )
    profile = analyze_repo(tmp_path)
    metrics = profile.metrics
    assert metrics.modules == 2
    assert metrics.test_modules_skipped == 1
    assert metrics.lines == 7
    assert (metrics.functions, metrics.methods, metrics.classes) == (1, 1, 1)
    assert metrics.average_complexity == 1.5
    assert metrics.max_complexity == 2
    assert metrics.complexity_ranks == {"A": 2}
    assert metrics.findings_by_severity == {"low": 1}
    assert metrics.findings_by_category == {"dead_code": 1}
    assert [m.path for m in profile.modules] == ["app/core.py", "app/util.py"]


def test_test_modules_are_not_analysed(tmp_path: Path) -> None:
    make_repo(tmp_path, {"tests/test_x.py": "assert eval('1')\n"})
    profile = analyze_repo(tmp_path)
    assert profile.findings == []
    assert profile.functions == []


def test_empty_input() -> None:
    profile = CodeAnalyzer().analyze(Path("/nonexistent"), [])
    assert profile.metrics.modules == 0
    assert profile.metrics.average_complexity == 0.0
    assert profile.metrics.max_complexity == 0


def test_unreadable_and_invalid_files_become_issues(tmp_path: Path) -> None:
    make_repo(tmp_path, {"big.py": "x = 1\n" * 50, "bad.py": "def f(:\n", "nul.py": "x = 1\x00\n"})
    (tmp_path / "link.py").symlink_to(tmp_path / "bad.py")
    profile = CodeAnalyzer(AnalysisLimits(max_read_bytes=100)).analyze(
        tmp_path, ["big.py", "bad.py", "nul.py", "link.py", "missing.py"]
    )
    issues = {(i.path, i.message.split(":")[0]) for i in profile.issues}
    assert issues == {
        ("big.py", "Not analysed (too large or unreadable)"),
        ("link.py", "Not analysed (too large or unreadable)"),
        ("missing.py", "Not analysed (too large or unreadable)"),
        ("bad.py", "Syntax error"),
        ("nul.py", "Not analysed (too large or unreadable)"),
    }
    assert profile.modules == []


def test_summary_excludes_details(tmp_path: Path) -> None:
    make_repo(tmp_path, {"a.py": "import os\n"})
    profile = analyze_repo(tmp_path)
    assert set(profile.summary().model_dump()) == {"metrics", "issues"}


def test_profile_round_trips_through_json(tmp_path: Path) -> None:
    make_repo(tmp_path, {"a.py": "def f(x=[]):\n    return eval(x)\n"})
    profile = analyze_repo(tmp_path)
    assert CodeProfile.model_validate_json(profile.model_dump_json()) == profile


# ------------------------------------------------------------------ golden: buggy_inventory
def expected_findings() -> set[tuple[str, str]]:
    """(rule_id, file) pairs listed in examples/buggy_inventory/EXPECTED_FINDINGS.md."""
    table = (EXAMPLES / "buggy_inventory" / "EXPECTED_FINDINGS.md").read_text()
    pairs: set[tuple[str, str]] = set()
    for row in table.splitlines():
        match = re.match(r"\| ([TM0-9 +]+) \| `(\w+)[./]", row)
        if match:
            for rule in match.group(1).split("+"):
                pairs.add((rule.strip(), match.group(2)))
    return pairs


@pytest.fixture(scope="module")
def buggy() -> CodeProfile:
    return analyze_repo(EXAMPLES / "buggy_inventory")


def test_buggy_inventory_matches_documented_defects(buggy: CodeProfile) -> None:
    found = {(f.rule_id, Path(f.path).stem) for f in buggy.findings}
    assert found == expected_findings()


def test_buggy_inventory_has_no_false_positives_on_safe_code(buggy: CodeProfile) -> None:
    symbols = {f.symbol for f in buggy.findings}
    assert "safe_ratio" not in symbols
    assert "apply_discount" not in symbols
    assert "Item.total_value" not in symbols


def test_buggy_inventory_severities(buggy: CodeProfile) -> None:
    assert buggy.metrics.findings_by_severity == {"critical": 1, "high": 5, "medium": 7, "low": 4}


@pytest.mark.parametrize("name", ["discount_service", "django_blog", "fastapi_todo", "flask_notes"])
def test_clean_examples_have_no_findings(name: str) -> None:
    profile = analyze_repo(EXAMPLES / name)
    assert profile.findings == []
    assert profile.issues == []
    assert profile.metrics.modules > 0
