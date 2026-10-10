"""The sandbox Python a project runs on, decided from what it declares."""

from __future__ import annotations

import pytest

from testmind.analyzers.repository.models import PythonRequirement
from testmind.sandbox.python_versions import (
    DEFAULT_PYTHON,
    SANDBOX_PYTHONS,
    choose_python,
    image_for,
)

ALL = list(SANDBOX_PYTHONS)


def choose(available: list[str] = ALL, **declared: object) -> tuple[str, str]:
    choice = choose_python(PythonRequirement.model_validate(declared), available)
    return choice.version, choice.reason


def test_the_table_covers_3_7_to_3_12_with_the_default() -> None:
    assert ALL == ["3.7", "3.8", "3.9", "3.10", "3.11", "3.12"]
    assert DEFAULT_PYTHON == "3.12"
    assert SANDBOX_PYTHONS["3.7"].pytest.startswith("7.")  # pytest 8 needs Python 3.8


@pytest.mark.parametrize(
    ("image", "version", "expected"),
    [
        ("testmind-sandbox:local", "3.12", "testmind-sandbox:local"),
        ("testmind-sandbox:local", "3.8", "testmind-sandbox:py3.8"),
        ("testmind-sandbox", "3.8", "testmind-sandbox:py3.8"),
        ("registry:5000/team/sandbox:v1", "3.9", "registry:5000/team/sandbox:py3.9"),
    ],
)
def test_image_for(image: str, version: str, expected: str) -> None:
    assert image_for(image, version) == expected


def test_nothing_declared_runs_on_the_newest() -> None:
    assert choose() == ("3.12", "the project declares no Python version; newest available")
    assert choose(available=[]) == (
        "3.12",
        "the project declares no Python version; newest available",
    )


def test_pinned_wins_when_available() -> None:
    assert choose(pinned="3.8", tested=["3.10"]) == ("3.8", "pinned to 3.8")
    assert choose(available=["3.12"], pinned="3.8") == (
        "3.12",
        "pinned to 3.8, which has no sandbox image; closest newer version",
    )
    assert choose(pinned="3.6")[0] == "3.7"


def test_newest_tested_version_within_the_declared_range() -> None:
    # thefuck: tox envlist py27..py38, no requires-python.
    assert choose(tested=["3.5", "3.6", "3.7", "3.8"]) == (
        "3.8",
        "newest version the project is tested on (3.5, 3.6, 3.7, 3.8)",
    )
    assert choose(requires=">=3.6,<3.10", tested=["3.6", "3.11"])[0] == "3.9"


def test_newest_allowed_by_the_range() -> None:
    assert choose(requires=">=3.7,<3.10") == (
        "3.9",
        "newest version allowed by requires-python >=3.7,<3.10",
    )


def test_tested_versions_without_images_run_on_the_closest_newer_one() -> None:
    # httpie in 2016: tox py26, py27, py35; classifiers up to 3.4.
    assert choose(tested=["3.1", "3.2", "3.3", "3.4", "3.5"]) == (
        "3.7",
        "the project is tested on 3.1, 3.2, 3.3, 3.4, 3.5, none of which has a sandbox image; "
        "closest newer version",
    )
    assert choose(available=["3.12"], tested=["3.6"])[0] == "3.12"
    # Tested only on versions newer than every image: the newest.
    assert choose(tested=["3.13"]) == (
        "3.12",
        "the project is tested on 3.13, none of which has a sandbox image; newest available",
    )


def test_an_unsatisfiable_range_runs_on_the_default_and_says_so() -> None:
    version, reason = choose(requires="<3.7")
    assert version == DEFAULT_PYTHON
    assert reason == (
        "no sandbox image satisfies requires-python <3.7; running on 3.12, "
        "where tests may fail for that reason"
    )


def test_unknown_available_versions_are_ignored() -> None:
    assert choose(available=["2.7", "3.8"], tested=["3.8"])[0] == "3.8"
