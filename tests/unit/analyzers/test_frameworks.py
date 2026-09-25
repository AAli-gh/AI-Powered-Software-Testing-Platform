from pathlib import Path

import pytest

from testmind.analyzers.repository.frameworks import is_test_file
from testmind.analyzers.repository.python_scanner import scan_python
from tests.unit.analyzers.helpers import analyze


def names_and_confidence(detections: list) -> dict[str, float]:  # type: ignore[type-arg]
    return {d.name: d.confidence for d in detections}


def test_import_alone_is_weak_evidence(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"util.py": "import flask\n"})
    assert names_and_confidence(profile.frameworks) == {"Flask": 0.3}


def test_no_evidence_means_no_detection(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"main.py": "print('hi')\n"})
    assert profile.frameworks == []
    assert profile.test_frameworks == []


def test_confidence_is_capped_and_evidence_is_explicit(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "requirements.txt": "fastapi\n",
            "app.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        },
    )
    fastapi = profile.frameworks[0]
    assert fastapi.confidence == 1.0
    assert fastapi.evidence == [
        "'fastapi' declared in requirements.txt",
        "'fastapi' imported in 1 file(s): app.py",
        "fastapi app instance in app.py:2",
    ]


def test_evidence_lists_are_abbreviated(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {f"m{i}.py": "import flask\n" for i in range(5)})
    assert profile.frameworks[0].evidence == [
        "'flask' imported in 5 file(s): m0.py, m1.py, m2.py (+2 more)"
    ]


def test_detections_sorted_by_confidence(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {"requirements.txt": "flask\n", "a.py": "import django\nimport flask\n"},
    )
    assert [d.name for d in profile.frameworks] == ["Flask", "Django"]


@pytest.mark.parametrize(
    ("config", "section"),
    [
        ({"pytest.ini": "[pytest]\n"}, "pytest.ini"),
        ({"setup.cfg": "[tool:pytest]\naddopts = -q\n"}, "setup.cfg [tool:pytest]"),
        ({"tox.ini": "[pytest]\n"}, "tox.ini [pytest]"),
        ({"pyproject.toml": "[tool.pytest.ini_options]\n"}, "pyproject.toml [tool.pytest]"),
    ],
)
def test_pytest_configuration_sources(tmp_path: Path, config: dict[str, str], section: str) -> None:
    profile = analyze(tmp_path, config)
    assert profile.test_frameworks[0].name == "pytest"
    assert profile.test_frameworks[0].evidence == [f"pytest configuration: {section}"]


def test_broken_config_files_do_not_count_as_pytest_config(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path, {"pyproject.toml": "[tool\n", "tox.ini": "garbage", "setup.cfg": "[x]\n"}
    )
    assert profile.test_frameworks == []


def test_unittest_from_testcase_classes(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path, {"tests/test_x.py": "import unittest\nclass T(unittest.TestCase): ...\n"}
    )
    assert names_and_confidence(profile.test_frameworks) == {"unittest": 1.0}


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("test_x.py", "", True),
        ("x_test.py", "", True),
        ("app/tests.py", "", True),
        ("tests/helpers.py", "def helper(): ...", False),
        ("tests/checks.py", "def test_a(): ...", True),
        ("test/suite.py", "class S(TestCase): ...", True),
        ("tests/conftest.py", "def test_like(): ...", False),
        ("tests/__init__.py", "", False),
        ("src/testing.py", "def test_a(): ...", False),
    ],
)
def test_is_test_file(path: str, source: str, expected: bool) -> None:
    assert is_test_file(scan_python(path, source)) is expected
