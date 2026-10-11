"""Which Python a repository declares, from each kind of file, without executing anything."""

from __future__ import annotations

import textwrap

import pytest

from testmind.analyzers.repository.models import PythonRequirement
from testmind.analyzers.repository.python_version import PythonVersionDetector


def detect(files: dict[str, str]) -> tuple[PythonRequirement, PythonVersionDetector]:
    contents = {path: textwrap.dedent(text) for path, text in files.items()}
    detector = PythonVersionDetector(contents.get)
    return detector.detect(contents), detector


def test_nothing_declared() -> None:
    requirement, detector = detect({"main.py": "print(1)\n"})
    assert requirement == PythonRequirement()
    assert detector.issues == []


def test_thefuck_layout_tox_envlist() -> None:
    # tox.ini and setup.py of thefuck at BugsInPy bug 1.
    requirement, _ = detect(
        {
            "tox.ini": """
            [tox]
            envlist = py27,py35,py36,py37,py38

            [testenv]
            commands = py.test -v --capture=sys
            """,
            "setup.py": "from setuptools import setup\nsetup(name='thefuck')\n",
        }
    )
    assert requirement.tested == ["3.5", "3.6", "3.7", "3.8"]  # py27 is not Python 3
    assert requirement.requires is None
    assert requirement.evidence[-1] == "tested on 3.8 (tox.ini envlist)"


def test_tox_brace_expansion_and_factors() -> None:
    requirement, _ = detect(
        {"tox.ini": "[tox]\nenvlist =\n    py{37,38}-django{22,30}\n    lint\n    py310\n"}
    )
    assert requirement.tested == ["3.7", "3.8", "3.10"]


def test_pinned_versions() -> None:
    pyenv, _ = detect({".python-version": "# pyenv\n3.8.10\n"})
    assert pyenv.pinned == "3.8"
    assert pyenv.evidence == ["pinned to 3.8 (.python-version)"]
    heroku, _ = detect({"runtime.txt": "python-3.9.7\n"})
    assert heroku.pinned == "3.9"
    for text in ("system\n", "pypy3.8\n", "\n"):
        unpinned, _ = detect({".python-version": text})
        assert unpinned.pinned is None


def test_pyproject_requires_python_and_classifiers() -> None:
    requirement, _ = detect(
        {
            "pyproject.toml": """
            [project]
            name = "pkg"
            requires-python = ">=3.8,<3.11"
            classifiers = [
                "Programming Language :: Python :: 3",
                "Programming Language :: Python :: 3.8",
                "Programming Language :: Python :: 3.10",
            ]
            """
        }
    )
    assert requirement.requires == ">=3.8,<3.11"
    assert requirement.tested == ["3.8", "3.10"]
    assert requirement.evidence == [
        "requires Python >=3.8,<3.11 (pyproject.toml)",
        "tested on 3.8 (pyproject.toml classifiers)",
        "tested on 3.10 (pyproject.toml classifiers)",
    ]


def test_flit_legacy_metadata() -> None:
    requirement, _ = detect(
        {
            "pyproject.toml": """
            [tool.flit.metadata]
            module = "fastapi"
            requires-python = ">=3.6"
            classifiers = [
                "Programming Language :: Python :: 3.7",
                "Programming Language :: Python :: 3.8",
            ]
            """
        }
    )
    assert requirement.requires == ">=3.6"
    assert requirement.tested == ["3.7", "3.8"]


@pytest.mark.parametrize(
    ("declared", "expected"),
    [("^3.8", ">=3.8,<4.0"), ("~3.9", ">=3.9,<3.10"), (">=3.7", ">=3.7")],
)
def test_poetry_python_dependency(declared: str, expected: str) -> None:
    requirement, _ = detect(
        {"pyproject.toml": f'[tool.poetry.dependencies]\npython = "{declared}"\n'}
    )
    assert requirement.requires == expected


def test_setup_cfg() -> None:
    requirement, _ = detect(
        {
            "setup.cfg": """
            [metadata]
            classifiers =
                Programming Language :: Python :: 3.7
                Topic :: Software Development

            [options]
            python_requires = >=3.7
            """
        }
    )
    assert (requirement.requires, requirement.tested) == (">=3.7", ["3.7"])


def test_setup_py_literals_and_constants() -> None:
    requirement, _ = detect(
        {
            "setup.py": """
            import setuptools
            CLASSIFIERS = ["Programming Language :: Python :: 3.6"]
            setuptools.setup(python_requires=">=3.6", classifiers=CLASSIFIERS)
            """
        }
    )
    assert (requirement.requires, requirement.tested) == (">=3.6", ["3.6"])
    dynamic, _ = detect({"setup.py": "from setuptools import setup\nsetup(python_requires=v())\n"})
    assert dynamic.requires is None


def test_the_first_declared_range_wins_and_sources_are_merged() -> None:
    requirement, _ = detect(
        {
            "pyproject.toml": '[project]\nrequires-python = ">=3.9"\n',
            "setup.cfg": "[options]\npython_requires = >=3.6\n",
            "tox.ini": "[tox]\nenvlist = py39\n",
            "setup.py": (
                "from setuptools import setup\n"
                "setup(classifiers=['Programming Language :: Python :: 3.9'])\n"
            ),
        }
    )
    assert requirement.requires == ">=3.9"
    assert requirement.evidence[-1] == "tested on 3.9 (setup.py classifiers, tox.ini envlist)"


def test_only_root_files_count() -> None:
    requirement, _ = detect({"docs/tox.ini": "[tox]\nenvlist = py36\n"})
    assert requirement.tested == []


@pytest.mark.parametrize(
    "files",
    [
        {"pyproject.toml": "[project\n"},
        {"pyproject.toml": "[tool]\npoetry = 1\n"},
        {"pyproject.toml": "[tool.poetry]\ndependencies = 1\n"},
        {"setup.cfg": "no section\n"},
        {"setup.py": "setup(\n"},
        {"setup.py": "f()(python_requires='>=3.6')\n"},
        {"setup.py": "from setuptools import setup\nsetup(classifiers='x')\n"},
        {"tox.ini": "[testenv]\ncommands = pytest\n"},
        {"pyproject.toml": '[project]\nrequires-python = ""\nclassifiers = 3\n'},
    ],
)
def test_malformed_or_silent_files_declare_nothing(files: dict[str, str]) -> None:
    requirement, _ = detect(files)
    assert (requirement.requires, requirement.tested) == (None, [])


def test_invalid_declarations_are_reported() -> None:
    requirement, detector = detect(
        {"pyproject.toml": '[project]\nrequires-python = "three point eight"\n'}
    )
    assert requirement.requires is None
    assert detector.issues[0].message == "Python requirement 'three point eight' is not PEP 440"
    _, tox = detect({"tox.ini": "[tox]\nenvlist = py38\nenvlist = py39\n"})
    assert tox.issues[0].message.startswith("Invalid tox.ini")


def test_partial_declarations() -> None:
    runtime, _ = detect({"runtime.txt": "3.9\n"})  # not Heroku's python-X.Y form
    assert runtime.pinned is None
    classifiers_only, _ = detect(
        {
            "setup.cfg": (
                "[metadata]\nclassifiers =\n"
                "    Programming Language :: Python :: 3.8\n"
                "    Programming Language :: Python :: 3.8\n"
            )
        }
    )
    assert classifiers_only.requires is None
    assert classifiers_only.evidence == ["tested on 3.8 (setup.cfg classifiers)"]
