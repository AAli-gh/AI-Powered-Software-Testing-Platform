from pathlib import Path

from testmind.analyzers.repository import AnalysisLimits, RepositoryAnalyzer
from tests.unit.analyzers.helpers import analyze, make_repo


def test_file_inventory(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "pkg/a.py": "x = 1\ny = 2\n",
            "pkg/b.py": "z = 3",
            "logo.png": b"\x89PNG\x00",
            "LICENSE": "MIT",
        },
    )
    by_path = {f.path: f for f in profile.files}
    assert profile.file_count == 4
    assert profile.total_size_bytes == sum(f.size_bytes for f in profile.files)
    assert by_path["pkg/a.py"].lines == 2
    assert by_path["pkg/b.py"].lines == 1  # no trailing newline
    assert by_path["logo.png"].language is None
    assert by_path["logo.png"].lines is None
    assert by_path["LICENSE"].lines is None  # unknown language: not counted
    assert profile.primary_language == "Python"
    assert profile.truncated is False


def test_empty_repository(tmp_path: Path) -> None:
    profile = RepositoryAnalyzer().analyze(tmp_path)
    assert profile.file_count == 0
    assert profile.primary_language is None
    assert profile.languages == []
    assert profile.issues == []


def test_empty_python_file_has_zero_lines(tmp_path: Path) -> None:
    assert analyze(tmp_path, {"__init__.py": ""}).files[0].lines == 0


def test_large_python_file_is_listed_but_not_parsed(tmp_path: Path) -> None:
    make_repo(tmp_path, {"huge.py": "import django\n" * 100, "small.py": "import flask\n"})
    profile = RepositoryAnalyzer(AnalysisLimits(max_read_bytes=100)).analyze(tmp_path)
    huge = next(f for f in profile.files if f.path == "huge.py")
    assert huge.lines is None
    assert [d.name for d in profile.frameworks] == ["Flask"]
    assert [(i.path, i.message) for i in profile.issues] == [
        ("huge.py", "Not analysed (too large or binary)")
    ]


def test_syntax_errors_are_issues_not_failures(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"broken.py": "def f(:\n", "ok.py": "import flask\n"})
    assert [d.name for d in profile.frameworks] == ["Flask"]
    assert profile.issues[0].path == "broken.py"
    assert profile.issues[0].message.startswith("Syntax error")


def test_stub_files_are_counted_but_not_scanned(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"types.pyi": "import flask\n"})
    assert profile.files[0].language == "Python"
    assert profile.frameworks == []


def test_truncation_is_reported(tmp_path: Path) -> None:
    make_repo(tmp_path, {f"m{i}.py": "" for i in range(4)})
    profile = RepositoryAnalyzer(AnalysisLimits(max_files=2)).analyze(tmp_path)
    assert profile.truncated is True
    assert profile.file_count == 2


def test_test_files_are_flagged(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"tests/test_a.py": "def test_x(): ...\n", "a.py": ""})
    assert profile.test_files == ["tests/test_a.py"]
    assert {f.path: f.is_test for f in profile.files} == {"a.py": False, "tests/test_a.py": True}


def test_profile_round_trips_through_json(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"requirements.txt": "flask\n", "app.py": "import flask\n"})
    assert type(profile).model_validate_json(profile.model_dump_json()) == profile


def test_oversized_manifests_are_skipped_without_errors(tmp_path: Path) -> None:
    padding = "#" * 200 + "\n"
    make_repo(
        tmp_path,
        {
            "pyproject.toml": padding + '[project.scripts]\nx = "a:b"\n',
            "docker-compose.yml": padding + "services: {api: {}}\n",
            ".github/workflows/ci.yml": padding + "jobs: {t: {}}\n",
        },
    )
    profile = RepositoryAnalyzer(AnalysisLimits(max_read_bytes=100)).analyze(tmp_path)
    assert profile.entry_points == []
    assert profile.docker.compose_services == []
    assert profile.ci[0].jobs == []
