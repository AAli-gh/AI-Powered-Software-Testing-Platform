"""Reading a BugsInPy bug: bug.info, run_test.sh and bug_patch.txt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

INFO_LINE = re.compile(r'^(\w+)\s*=\s*"(.*)"\s*$')
PYTEST_TARGET = re.compile(r"(?<![\w/.-])([\w/.-]+\.py(?:::[\w\[\]\-.:]+)?)")
UNITTEST_TARGET = re.compile(r"\bunittest\b(?:\s+-\w+)*\s+([\w.]+)")


@dataclass(frozen=True, slots=True)
class Bug:
    project: str
    number: int
    repository: str  # https://github.com/owner/name
    buggy_commit: str
    fixed_commit: str
    python_version: str  # as BugsInPy recorded it, e.g. 3.8.3
    targets: tuple[str, ...]  # the failing tests, as written in run_test.sh
    patch: str  # the source fix, buggy -> fixed

    @property
    def name(self) -> str:
        return f"{self.project}-{self.number}"


def parse_info(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in map(INFO_LINE.match, text.splitlines()) if m}


def parse_targets(run_test: str) -> tuple[str, ...]:
    """The tests a run_test.sh runs: pytest node ids (`a.py::T::test`, also through tox) or
    unittest names (`tests.test_a.T.test`, resolved against a tree by `to_node_id`)."""
    targets: list[str] = []
    for line in run_test.splitlines():
        unittest = UNITTEST_TARGET.search(line)
        if unittest:
            targets.append(unittest.group(1))
            continue
        targets.extend(m.group(1) for m in PYTEST_TARGET.finditer(line))
    return tuple(dict.fromkeys(targets))


def to_node_id(target: str, root: Path) -> str | None:
    """A pytest node id for a target: as is when it names a file, else the longest dotted
    prefix that is a module of the tree (`tests.test_a.T.test` -> `tests/test_a.py::T::test`)."""
    if ".py" in target.split("::", 1)[0]:
        return target
    parts = target.split(".")
    for end in range(len(parts), 0, -1):
        path = "/".join(parts[:end]) + ".py"
        if (root / path).is_file():
            return "::".join([path, *parts[end:]])
    return None


def load_bug(projects: Path, project: str, number: int) -> Bug:
    directory = projects / project / "bugs" / str(number)
    info = parse_info((directory / "bug.info").read_text("utf-8"))
    project_info = parse_info((projects / project / "project.info").read_text("utf-8"))
    return Bug(
        project=project,
        number=number,
        repository=project_info["github_url"].rstrip("/"),
        buggy_commit=info["buggy_commit_id"],
        fixed_commit=info["fixed_commit_id"],
        python_version=info.get("python_version", ""),
        targets=parse_targets((directory / "run_test.sh").read_text("utf-8")),
        patch=(directory / "bug_patch.txt").read_text("utf-8", errors="replace"),
    )


def bug_numbers(projects: Path, project: str) -> list[int]:
    bugs = projects / project / "bugs"
    return sorted(int(p.name) for p in bugs.iterdir() if p.name.isdigit())
