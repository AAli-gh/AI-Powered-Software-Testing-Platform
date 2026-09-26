from testmind.regression.diff import FunctionStatus, diff_module

BASE = '''\
"""Module docstring."""
import os

LIMIT = 10


def unchanged(x):
    return x


def reformatted(a, b):
    return a+b


def documented(x):
    return x * 2


def modified(x):
    return x + 1


def removed():
    pass


class Shop:
    rate = 1

    def price(self, n):
        return n * self.rate

    @staticmethod
    def decorated():
        return 1
'''

HEAD = '''\
"""Module docstring, reworded."""
import os

LIMIT = 10


def unchanged(x):
    # a new comment
    return x


def reformatted(
    a,
    b,
):
    return (a + b)


def documented(x):
    """Now with a docstring."""
    return x * 2


def modified(x):
    return x + 2


class Shop:
    rate = 1

    def price(self, n):
        return n * self.rate

    @classmethod
    def decorated(cls):
        return 1


def added():
    return 3
'''


def statuses(base: str | None, head: str | None) -> dict[str, FunctionStatus]:
    return {c.qualname: c.status for c in diff_module("shop/m.py", base, head).functions}


def test_only_real_code_changes_count() -> None:
    diff = diff_module("shop/m.py", BASE, HEAD)
    assert statuses(BASE, HEAD) == {
        "modified": FunctionStatus.MODIFIED,
        "removed": FunctionStatus.REMOVED,
        "Shop.decorated": FunctionStatus.MODIFIED,  # decorator and signature changed
        "added": FunctionStatus.ADDED,
    }
    # Comments, formatting, redundant parentheses and docstrings are not changes.
    assert diff.module_level_changed is False
    modified = next(c for c in diff.functions if c.qualname == "modified")
    assert (modified.base_lines, modified.head_lines) == ((19, 20), (24, 25))
    removed = next(c for c in diff.functions if c.qualname == "removed")
    assert (removed.base_lines, removed.head_lines) == ((23, 24), None)


def test_module_level_changes() -> None:
    assert diff_module("m.py", "import os\nX = 1\n", "import os\nX = 2\n").module_level_changed
    assert diff_module("m.py", "import os\n", "import sys\n").module_level_changed
    class_attribute = diff_module("m.py", "class A:\n    rate = 1\n", "class A:\n    rate = 2\n")
    assert class_attribute.module_level_changed
    method_only = diff_module(
        "m.py",
        "class A:\n    def f(self):\n        return 1\n",
        "class A:\n    def f(self):\n        return 2\n",
    )
    assert not method_only.module_level_changed
    assert [(c.qualname, c.status) for c in method_only.functions] == [
        ("A.f", FunctionStatus.MODIFIED)
    ]


def test_nested_function_changes_the_enclosing_one() -> None:
    base = "def outer():\n    def inner():\n        return 1\n    return inner()\n"
    head = "def outer():\n    def inner():\n        return 2\n    return inner()\n"
    assert statuses(base, head) == {
        "outer": FunctionStatus.MODIFIED,
        "outer.<locals>.inner": FunctionStatus.MODIFIED,
    }


def test_added_and_deleted_files() -> None:
    assert statuses(None, "def f():\n    pass\n") == {"f": FunctionStatus.ADDED}
    deleted = diff_module("m.py", "X = 1\ndef f():\n    pass\n", None)
    assert [(c.qualname, c.status) for c in deleted.functions] == [("f", FunctionStatus.REMOVED)]
    assert deleted.module_level_changed


def test_unparsable_version_is_a_change_throughout() -> None:
    diff = diff_module("m.py", "def f():\n    pass\n", "def f(:\n")
    assert diff.functions == []
    assert diff.module_level_changed
    assert diff.parse_error is not None
    assert diff.parse_error.startswith("m.py does not parse:")
