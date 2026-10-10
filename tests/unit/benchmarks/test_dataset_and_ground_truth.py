"""Reading BugsInPy bugs and computing what their fixes changed."""

from __future__ import annotations

import textwrap
from pathlib import Path

from benchmarks.bugsinpy.dataset import bug_numbers, load_bug, parse_info, parse_targets, to_node_id
from benchmarks.bugsinpy.ground_truth import (
    MODULE,
    Location,
    changed_lines,
    enclosing_functions,
    ground_truth,
)

# thefuck bug 1, as BugsInPy ships it.
BLANK = " "  # an empty context line of a diff
THEFUCK_1 = f"""\
diff --git a/thefuck/rules/pip_unknown_command.py b/thefuck/rules/pip_unknown_command.py
index 75fcc7c..2720cda 100644
--- a/thefuck/rules/pip_unknown_command.py
+++ b/thefuck/rules/pip_unknown_command.py
@@ -12,8 +12,8 @@ def match(command):
{BLANK}
{BLANK}
 def get_new_command(command):
-    broken_cmd = re.findall(r'ERROR: unknown command \\"([a-z]+)\\"',
+    broken_cmd = re.findall(r'ERROR: unknown command "([^"]+)"',
                             command.output)[0]
-    new_cmd = re.findall(r'maybe you meant \\"([a-z]+)\\"', command.output)[0]
+    new_cmd = re.findall(r'maybe you meant "([^"]+)"', command.output)[0]
{BLANK}
     return replace_argument(command.script, broken_cmd, new_cmd)
"""
THEFUCK_1_BUGGY = "\n" * 13 + textwrap.dedent(
    """\
    def get_new_command(command):
        broken_cmd = re.findall(r'x', command.output)[0]
        new_cmd = re.findall(r'y', command.output)[0]

        return replace_argument(command.script, broken_cmd, new_cmd)
    """
)


def test_parse_info() -> None:
    text = 'python_version="3.7.0"\nbuggy_commit_id="abc"\nnot a pair\n'
    assert parse_info(text) == {"python_version": "3.7.0", "buggy_commit_id": "abc"}


def test_parse_targets_in_every_form() -> None:
    assert parse_targets("pytest tests/rules/test_pip.py::test_get_new_command") == (
        "tests/rules/test_pip.py::test_get_new_command",
    )
    assert parse_targets("pytest -q -s tests/test_chinese.py::test_chinese\n") == (
        "tests/test_chinese.py::test_chinese",
    )
    assert parse_targets("python3 -m pytest tqdm/tests/tests_tqdm.py::test_update") == (
        "tqdm/tests/tests_tqdm.py::test_update",
    )
    assert parse_targets("tox tests/test_hooks.py::TestExternalHooks::test_run_hook") == (
        "tests/test_hooks.py::TestExternalHooks::test_run_hook",
    )
    assert parse_targets(
        "python -m unittest -q tests.test_black.BlackTestCase.test_python37\n"
        "python -m unittest -q tests.test_black.BlackTestCase.test_python37\n"
    ) == ("tests.test_black.BlackTestCase.test_python37",)  # duplicates once
    assert parse_targets("pytest a.py::t1\npytest b.py::C::t2") == ("a.py::t1", "b.py::C::t2")


def test_to_node_id(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_black.py").write_text("")
    assert to_node_id("a.py::t", tmp_path) == "a.py::t"
    assert to_node_id("tests.test_black.BlackTestCase.test_x", tmp_path) == (
        "tests/test_black.py::BlackTestCase::test_x"
    )
    assert to_node_id("tests.test_missing.T.t", tmp_path) is None


def test_load_bug(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "thefuck"
    bug = project / "bugs" / "1"
    bug.mkdir(parents=True)
    (project / "bugs" / "10").mkdir()
    (project / "bugs" / "notes").mkdir()
    (project / "project.info").write_text('github_url="https://github.com/nvbn/thefuck/"\n')
    (bug / "bug.info").write_text(
        'python_version="3.7.0"\nbuggy_commit_id="b1"\nfixed_commit_id="f1"\n'
    )
    (bug / "run_test.sh").write_text("pytest tests/t.py::test_a")
    (bug / "bug_patch.txt").write_text(THEFUCK_1)
    loaded = load_bug(tmp_path / "projects", "thefuck", 1)
    assert (loaded.name, loaded.repository, loaded.fixed_commit, loaded.targets) == (
        "thefuck-1", "https://github.com/nvbn/thefuck", "f1", ("tests/t.py::test_a",),
    )  # fmt: skip
    assert bug_numbers(tmp_path / "projects", "thefuck") == [1, 10]


def test_changed_lines_of_a_modification() -> None:
    assert changed_lines(THEFUCK_1) == {"thefuck/rules/pip_unknown_command.py": {15, 17}}


def test_pure_insertions_deletions_and_other_files() -> None:
    patch = textwrap.dedent(
        """\
        --- a/pkg/mod.py
        +++ b/pkg/mod.py
        @@ -5,3 +5,4 @@
         def f():
        +    check()
             return 1
         x = 2
        @@ -20 +21,0 @@
        -removed = True
        --- a/README.md
        +++ b/README.md
        @@ -1 +1 @@
        -old
        +new
        --- /dev/null
        +++ b/pkg/new.py
        @@ -0,0 +1 @@
        +created = True
        """
    )
    assert changed_lines(patch) == {"pkg/mod.py": {5, 20}}


def test_enclosing_functions() -> None:
    source = textwrap.dedent(
        """\
        import os

        class Shell:
            @property
            def name(self):
                return "x"

            def run(self):
                def helper():
                    return 1
                return helper()

        async def fetch():
            return await other()
        """
    )
    assert enclosing_functions(source, {1, 4, 6, 10, 11, 14}) == {
        1: MODULE,
        4: "Shell.name",  # the decorator belongs to the function
        6: "Shell.name",
        10: "Shell.run.<locals>.helper",
        11: "Shell.run",
        14: "fetch",
    }


def test_ground_truth() -> None:
    path = "thefuck/rules/pip_unknown_command.py"
    truth = ground_truth(THEFUCK_1, {path: THEFUCK_1_BUGGY})
    assert truth.functions == {Location(path, "get_new_command")}
    assert truth.files == {path}
    unparsable = ground_truth(THEFUCK_1, {path: "def broken(:\n"})
    assert (unparsable.files, unparsable.functions) == ({path}, set())
    unread = ground_truth(THEFUCK_1, {})
    assert unread.functions == set()
