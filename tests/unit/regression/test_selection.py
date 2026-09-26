from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryAnalyzer, RepositoryProfile
from testmind.coverage_engine.impact import TestImpact
from testmind.regression.diff import diff_module
from testmind.regression.models import ChangedFile, SelectedTarget, Selection
from testmind.regression.selection import (
    classify,
    is_selected,
    pytest_targets,
    select_tests,
    short_nodeid,
)
from testmind.vcs import FileChange, FileStatus
from tests.regression_fixture import FILES, PRICING_BASE, PRICING_HEAD
from tests.unit.analyzers.helpers import make_repo

IMPACT = TestImpact(
    functions={
        "shop/pricing.py::discount": [
            "tests/test_cart.py::TestCart::test_total",
            "tests/test_pricing.py::test_discount",
            "tests/test_gone.py::test_old",  # a test file deleted at head
        ],
        "shop/pricing.py::tax": ["tests/test_pricing.py::test_tax"],
    },
    tests=4,
)


@pytest.fixture
def head(tmp_path: Path) -> tuple[Path, RepositoryProfile, CodeProfile]:
    root = make_repo(tmp_path, {**FILES, "shop/pricing.py": PRICING_HEAD, "requirements.txt": ""})
    repository = RepositoryAnalyzer().analyze(root)
    python = [f.path for f in repository.files if f.language == "Python"]
    return root, repository, CodeAnalyzer().analyze(root, python, repository.test_files)


def select(
    head: tuple[Path, RepositoryProfile, CodeProfile],
    files: list[ChangedFile],
    diffs: dict[str, object],
    impact: TestImpact | None = IMPACT,
) -> Selection:
    root, repository, code = head
    return select_tests(
        files,
        diffs,  # type: ignore[arg-type]
        head=repository,
        head_code=code,
        read_head=lambda path: (root / path).read_text() if (root / path).is_file() else None,
        base_impact=impact,
    )


def changed(path: str, kind: str = "production", status: str = "modified") -> ChangedFile:
    return ChangedFile(path=path, status=status, kind=kind)


def targets(selection: Selection) -> dict[str, list[str]]:
    return {t.target: t.reasons for t in selection.targets}


def test_modified_functions_select_the_tests_that_ran_them(
    head: tuple[Path, RepositoryProfile, CodeProfile],
) -> None:
    diff = diff_module("shop/pricing.py", PRICING_BASE, PRICING_HEAD)
    selection = select(head, [changed("shop/pricing.py")], {"shop/pricing.py": diff})

    assert selection.everything is False
    assert targets(selection) == {
        "tests/test_cart.py::TestCart::test_total": ["executed discount (modified) at base"],
        "tests/test_pricing.py::test_discount": ["executed discount (modified) at base"],
        # `rebate` is new: every test file importing shop.pricing, directly or via shop.cart
        "tests/test_cart.py": ["imports shop.pricing (changed)"],
        "tests/test_pricing.py": ["imports shop.pricing (changed)"],
    }
    assert selection.notes == []


def test_untested_changes_are_reported(head: tuple[Path, RepositoryProfile, CodeProfile]) -> None:
    diff = diff_module(
        "shop/unused.py", "def helper():\n    return 1\n", "def helper():\n    return 2\n"
    )
    selection = select(head, [changed("shop/unused.py")], {"shop/unused.py": diff})
    assert selection.targets == []
    assert selection.notes == [
        "shop/unused.py::helper (modified) was not executed by any test at base"
    ]

    module_level = diff_module("shop/unused.py", "X = 1\n", "X = 2\n")
    selection = select(head, [changed("shop/unused.py")], {"shop/unused.py": module_level})
    assert selection.notes == ["No test imports shop.unused, directly or indirectly"]


def test_without_per_test_coverage_imports_decide(
    head: tuple[Path, RepositoryProfile, CodeProfile],
) -> None:
    diff = diff_module(
        "shop/labels.py", "def label(n):\n    return n\n", "def label(n):\n    return 2\n"
    )
    selection = select(head, [changed("shop/labels.py")], {"shop/labels.py": diff}, impact=None)
    assert targets(selection) == {"tests/test_labels.py": ["imports shop.labels (changed)"]}
    assert selection.notes == [
        "No per-test coverage at base: tests were selected through imports only"
    ]


def test_configuration_changes_select_everything(
    head: tuple[Path, RepositoryProfile, CodeProfile],
) -> None:
    selection = select(head, [changed("requirements.txt", "configuration")], {})
    assert selection.everything is True
    assert sorted(targets(selection)) == [
        "tests/test_cart.py", "tests/test_labels.py", "tests/test_pricing.py",
    ]  # fmt: skip
    assert targets(selection)["tests/test_cart.py"] == [
        "configuration or dependencies changed: requirements.txt"
    ]


def test_changed_test_files_run(head: tuple[Path, RepositoryProfile, CodeProfile]) -> None:
    selection = select(
        head,
        [changed("tests/test_labels.py", "test"), changed("tests/test_old.py", "test", "deleted")],
        {},
    )
    assert targets(selection) == {"tests/test_labels.py": ["test file modified"]}


def test_unparsable_module(head: tuple[Path, RepositoryProfile, CodeProfile]) -> None:
    diff = diff_module("shop/labels.py", "def label(n):\n    return n\n", "def label(:\n")
    selection = select(head, [changed("shop/labels.py")], {"shop/labels.py": diff})
    assert targets(selection) == {"tests/test_labels.py": ["imports shop.labels (changed)"]}
    assert selection.notes[0].startswith("shop/labels.py does not parse")


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("requirements-dev.txt", "configuration"),
        ("pyproject.toml", "configuration"),
        ("tests/conftest.py", "configuration"),
        ("blog/settings.py", "configuration"),  # the Django settings module
        ("tests/test_pricing.py", "test"),
        ("tests/helpers.py", "test"),
        ("shop/pricing.py", "production"),
        ("README.md", "other"),
    ],
)
def test_classify(tmp_path: Path, path: str, kind: str) -> None:
    profile = RepositoryAnalyzer().analyze(
        make_repo(
            tmp_path,
            {
                "manage.py": (
                    "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blog.settings')\n"
                ),
                "tests/test_pricing.py": "def test_a(): pass\n",
            },
        )
    )
    assert classify(FileChange(path, FileStatus.MODIFIED), profile, profile).kind == kind


def test_targets_for_pytest() -> None:
    selection = Selection(
        everything=False,
        targets=[
            SelectedTarget(target="tests/a.py", reasons=["r"]),
            SelectedTarget(target="tests/a.py::test_x", reasons=["r"]),
            SelectedTarget(target="tests/b.py::TestB::test_y[1]", reasons=["r"]),
        ],
        notes=[],
    )
    # pytest collects only the nodeid when given a file and a nodeid inside it.
    assert pytest_targets(selection) == ("tests/a.py", "tests/b.py::TestB::test_y[1]")
    assert is_selected(selection, "tests/a.py::test_other")
    assert is_selected(selection, "tests/b.py::test_y[1]")
    assert not is_selected(selection, "tests/b.py::test_z")
    assert short_nodeid("tests/b.py::TestB::test_y[1]") == "tests/b.py::test_y[1]"
    assert short_nodeid("tests/b.py") == "tests/b.py"


def test_unreadable_or_broken_test_files_are_skipped_in_the_import_graph(
    head: tuple[Path, RepositoryProfile, CodeProfile],
) -> None:
    root, repository, code = head
    (root / "tests" / "test_labels.py").write_text("import shop.labels\ndef broken(:\n")
    diff = diff_module("shop/labels.py", "X = 1\n", "X = 2\n")
    selection = select_tests(
        [changed("shop/labels.py")],
        {"shop/labels.py": diff},
        head=repository,
        head_code=code,
        read_head=lambda path: None if path.endswith("test_cart.py") else (root / path).read_text(),
        base_impact=IMPACT,
    )
    # test_labels.py does not parse and test_cart.py could not be read: neither is guessed.
    assert selection.targets == []
    assert selection.notes == ["No test imports shop.labels, directly or indirectly"]
