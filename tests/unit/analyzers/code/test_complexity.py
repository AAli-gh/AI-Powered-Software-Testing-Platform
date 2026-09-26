import ast
import textwrap

import pytest

from testmind.analyzers.code.complexity import complexity_rank, cyclomatic_complexity


def complexity(source: str) -> int:
    function = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef)
    return cyclomatic_complexity(function)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def f(): return 1", 1),
        ("def f(x):\n    if x: return 1\n    elif x > 2: return 2\n    return 3", 3),
        ("def f(xs):\n    for x in xs:\n        while x: x -= 1", 3),
        ("async def f(xs):\n    async for x in xs: pass", 2),
        ("def f(a, b, c): return a and b or c", 3),
        ("def f(a): return 1 if a else 2", 2),
        ("def f():\n    try: pass\n    except A: pass\n    except B: pass", 3),
        ("def f(xs): return [x for x in xs if x if x > 1]", 4),
        ("def f(x):\n    match x:\n        case 1: pass\n        case _: pass", 3),
    ],
)
def test_decision_points(source: str, expected: int) -> None:
    assert complexity(source) == expected


def test_nested_scopes_do_not_count() -> None:
    source = """
    def outer(x):
        def inner(y):
            if y: return 1
        class K:
            def m(self):
                if self: pass
        g = lambda z: 1 if z else 2
        return inner
    """
    assert complexity(source) == 1


@pytest.mark.parametrize(
    ("value", "rank"),
    [
        (1, "A"),
        (5, "A"),
        (6, "B"),
        (10, "B"),
        (11, "C"),
        (20, "C"),
        (21, "D"),
        (31, "E"),
        (41, "F"),
    ],
)
def test_rank(value: int, rank: str) -> None:
    assert complexity_rank(value) == rank
