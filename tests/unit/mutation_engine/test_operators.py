import ast
from pathlib import Path

from testmind.analyzers.code import CodeAnalyzer
from testmind.mutation_engine.operators import Mutant, Operator, generate_mutants
from tests.unit.analyzers.helpers import make_repo

SOURCE = '''\
"""Module docstring: not mutated."""
LIMIT = 10  # module level: not mutated


def check(x: int = 3, *, flag: bool = True) -> bool:
    """Docstring: not mutated."""
    label = f"value {x + 1}"
    if x == 0:
        return False
    if x < LIMIT and not flag:
        return x * 2 > 5
    return "a" + label is None


class Box:
    size: int = 4

    def grow(self, n):
        self.size += n % 3
        return None
'''


def mutants(tmp_path: Path, source: str = SOURCE) -> list[tuple[int, Operator, str, str]]:
    root = make_repo(tmp_path, {"m.py": source})
    code = CodeAnalyzer().analyze(root, ["m.py"])
    return [
        (m.line, m.operator, m.original, m.replacement)
        for m in generate_mutants("m.py", source, code.functions)
    ]


def test_mutants_of_every_operator(tmp_path: Path) -> None:
    assert mutants(tmp_path) == [
        (8, Operator.COMPARISON, "x == 0", "(x != 0)"),
        (8, Operator.CONSTANT, "0", "(1)"),
        (9, Operator.RETURN, "return False", "return None"),
        (9, Operator.CONSTANT, "False", "True"),
        (10, Operator.COMPARISON, "x < LIMIT", "(x <= LIMIT)"),
        (10, Operator.CONDITION, "x < LIMIT and not flag", "(not (x < LIMIT and not flag))"),
        (10, Operator.BOOLEAN, "x < LIMIT and not flag", "(x < LIMIT or not flag)"),
        (10, Operator.NEGATION, "not flag", "(flag)"),
        (11, Operator.RETURN, "return x * 2 > 5", "return None"),
        (11, Operator.ARITHMETIC, "x * 2", "(x / 2)"),
        (11, Operator.COMPARISON, "x * 2 > 5", "(x * 2 >= 5)"),
        (11, Operator.CONSTANT, "2", "(3)"),
        (11, Operator.CONSTANT, "5", "(6)"),
        (12, Operator.RETURN, 'return "a" + label is None', "return None"),
        (12, Operator.COMPARISON, '"a" + label is None', "('a' + label is not None)"),
        (19, Operator.ARITHMETIC, "n % 3", "(n * 3)"),
        (19, Operator.CONSTANT, "3", "(4)"),
    ]
    # Not mutated: docstrings, the module level (LIMIT), annotations, the default value on
    # the `def` line, the f-string, the string concatenation, `return None` and `size: int`.


def test_every_mutant_applies_exactly_and_compiles(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"m.py": SOURCE})
    code = CodeAnalyzer().analyze(root, ["m.py"])
    data = SOURCE.encode()
    seen = set()
    for mutant in generate_mutants("m.py", SOURCE, code.functions):
        assert data[mutant.start : mutant.end].decode() == mutant.original
        mutated = mutant.apply(data)
        ast.parse(mutated)
        assert mutated not in seen
        seen.add(mutated)
        assert mutant.function in {"check", "Box.grow"}
        assert len(mutant.id) == 12


def test_non_ascii_offsets_are_bytes(tmp_path: Path) -> None:
    source = 'def f(a):\n    s = "héllo"\n    return len(s) + a\n'
    [ret, arith] = [
        m
        for m in generate_mutants_for(tmp_path, source)
        if m.operator in {Operator.RETURN, Operator.ARITHMETIC}
    ]
    assert arith.original == "len(s) + a"
    assert arith.apply(source.encode()).decode() == source.replace("len(s) + a", "(len(s) - a)")
    assert ret.replacement == "return None"


def generate_mutants_for(tmp_path: Path, source: str) -> list[Mutant]:
    root = make_repo(tmp_path, {"m.py": source})
    code = CodeAnalyzer().analyze(root, ["m.py"])
    return generate_mutants("m.py", source, code.functions)


def test_unparsable_source_has_no_mutants() -> None:
    assert generate_mutants("m.py", "def f(:\n", []) == []


def test_mutants_that_would_not_compile_are_dropped(tmp_path: Path) -> None:
    # A mapping pattern key must be a plain literal: `case {(2): y}` is a syntax error.
    source = "def f(v):\n    match v:\n        case {1: y}:\n            return y\n"
    kept = {(m.original, m.replacement) for m in generate_mutants_for(tmp_path, source)}
    assert ("1", "(2)") not in kept
    assert ("return y", "return None") in kept


def test_decorators_of_nested_functions_are_not_mutated(tmp_path: Path) -> None:
    source = (
        "import functools\n\n\ndef outer():\n"
        "    @functools.lru_cache(maxsize=2)\n    def inner(x):\n        return x\n"
        "    return inner\n"
    )
    originals = {m.original for m in generate_mutants_for(tmp_path, source)}
    assert "2" not in originals
    assert originals == {"return x", "return inner"}
