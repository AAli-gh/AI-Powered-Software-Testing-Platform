"""Deterministic mutant generation from the AST.

Every mutant is one small change at an exact position: a byte range of the file and its
replacement. A mutant is kept only if the mutated file still compiles, and identical results
are generated once.

Mutation happens only inside functions. Module-level code runs at import, so a mutant there
changes every test at once, which says little about any of them. Docstrings, type annotations
and f-string parts are never mutated: changing them does not change behaviour.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum

from testmind.analyzers.code.models import FunctionInfo


class Operator(StrEnum):
    ARITHMETIC = "arithmetic"  # a + b → a - b, a * b → a / b, …
    COMPARISON = "comparison"  # < → <=, == → !=, in → not in, …
    BOOLEAN = "boolean"  # and ↔ or
    NEGATION = "negation"  # not x → x
    CONDITION = "condition"  # if c: → if not (c):
    CONSTANT = "constant"  # n → n + 1, True ↔ False
    RETURN = "return"  # return x → return None


ARITHMETIC: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult,
    ast.FloorDiv: ast.Div, ast.Mod: ast.Mult, ast.Pow: ast.Mult,
}  # fmt: skip
COMPARISON: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq, ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In,
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Mutant:
    id: str
    path: str
    function: str  # qualname of the enclosing function
    line: int
    end_line: int
    column: int
    operator: Operator
    start: int  # byte offsets into the UTF-8 file
    end: int
    original: str
    replacement: str

    def apply(self, source: bytes) -> bytes:
        return source[: self.start] + self.replacement.encode() + source[self.end :]


def generate_mutants(path: str, source: str, functions: list[FunctionInfo]) -> list[Mutant]:
    """All mutants of the given functions of one file, in source order."""
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return []
    data = source.encode()
    offsets = _line_offsets(data)
    skipped = _unmutable(tree)
    ranges = sorted(((f.line, f.end_line, f.qualname) for f in functions), key=lambda r: r[0])

    mutants: dict[str, Mutant] = {}
    seen_results: set[bytes] = set()
    for node in ast.walk(tree):
        if id(node) in skipped or not hasattr(node, "lineno"):
            continue
        function = _enclosing(ranges, node.lineno)
        if function is None:
            continue
        for operator, target, replacement in _mutations(node, data, offsets):
            start = offsets[target.lineno - 1] + target.col_offset
            end = offsets[_end_line(target) - 1] + _end_col(target)
            mutant = Mutant(
                id=_mutant_id(path, start, end, replacement),
                path=path,
                function=function,
                line=target.lineno,
                end_line=_end_line(target),
                column=target.col_offset + 1,
                operator=operator,
                start=start,
                end=end,
                original=data[start:end].decode("utf-8", "replace"),
                replacement=replacement,
            )
            result = mutant.apply(data)
            if result in seen_results or not _compiles(result, path):
                continue
            seen_results.add(result)
            mutants[mutant.id] = mutant
    return sorted(mutants.values(), key=lambda m: (m.start, m.end, m.replacement))


# ---------------------------------------------------------------------------------- operators
def _mutations(
    node: ast.AST, data: bytes, offsets: list[int]
) -> Iterator[tuple[Operator, ast.expr | ast.stmt, str]]:
    def text(part: ast.expr) -> str:
        start = offsets[part.lineno - 1] + part.col_offset
        end = offsets[_end_line(part) - 1] + _end_col(part)
        return data[start:end].decode("utf-8", "replace")

    if isinstance(node, ast.BinOp) and type(node.op) in ARITHMETIC and not _is_text(node):
        arithmetic = copy.deepcopy(node)
        arithmetic.op = ARITHMETIC[type(node.op)]()
        yield Operator.ARITHMETIC, node, f"({ast.unparse(arithmetic)})"
    elif isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in COMPARISON:
        comparison = copy.deepcopy(node)
        comparison.ops = [COMPARISON[type(node.ops[0])]()]
        yield Operator.COMPARISON, node, f"({ast.unparse(comparison)})"
    elif isinstance(node, ast.BoolOp):
        boolean = copy.deepcopy(node)
        boolean.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        yield Operator.BOOLEAN, node, f"({ast.unparse(boolean)})"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield Operator.NEGATION, node, f"({text(node.operand)})"
    elif isinstance(node, ast.If) and not _negated_by_comparison(node.test):
        yield Operator.CONDITION, node.test, f"(not ({text(node.test)}))"
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        yield Operator.CONSTANT, node, str(not node.value)
    elif isinstance(node, ast.Constant) and type(node.value) is int:
        yield Operator.CONSTANT, node, f"({node.value + 1})"
    elif isinstance(node, ast.Return) and node.value is not None and not _is_none(node.value):
        yield Operator.RETURN, node, "return None"


def _unmutable(tree: ast.Module) -> set[int]:
    """ids of nodes whose mutation cannot change behaviour: docstrings, annotations and
    everything inside them, and f-strings (their parts are not independent expressions)."""
    skipped: set[int] = set()

    def skip(node: ast.AST | None) -> None:
        if node is not None:
            skipped.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip(body[0])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            skip(node.returns)
            for decorator in node.decorator_list:
                skip(decorator)
        elif isinstance(node, ast.arg | ast.AnnAssign):
            skip(node.annotation)
        elif isinstance(node, ast.JoinedStr):
            skip(node)
    return skipped


# ---------------------------------------------------------------------------------- helpers
def _enclosing(ranges: list[tuple[int, int, str]], line: int) -> str | None:
    """The innermost function whose body contains `line` (its `def` line excluded)."""
    inside = [r for r in ranges if r[0] < line <= r[1]]
    return max(inside, key=lambda r: r[0])[2] if inside else None


def _is_text(node: ast.BinOp) -> bool:
    """`"a" + b` / `"%s" % x` are string operations; their arithmetic mutants only crash."""
    return any(
        (isinstance(side, ast.Constant) and isinstance(side.value, str | bytes))
        or isinstance(side, ast.JoinedStr)
        for side in (node.left, node.right)
    )


NEGATING_COMPARISONS = (ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.In, ast.NotIn)


def _negated_by_comparison(test: ast.expr) -> bool:
    """`if a == b` negated is `if a != b`, which the comparison operator already produces."""
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], NEGATING_COMPARISONS)
    )


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _line_offsets(data: bytes) -> list[int]:
    offsets = [0]
    for index, byte in enumerate(data):
        if byte == ord("\n"):
            offsets.append(index + 1)
    return offsets


def _end_line(node: ast.expr | ast.stmt) -> int:
    return node.end_lineno if node.end_lineno is not None else node.lineno


def _end_col(node: ast.expr | ast.stmt) -> int:
    return node.end_col_offset if node.end_col_offset is not None else node.col_offset


def _compiles(source: bytes, path: str) -> bool:
    """Warnings (the project's own `is` with a literal, say) are not the mutant's business."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            compile(source, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError):
            return False
    return True


def _mutant_id(path: str, start: int, end: int, replacement: str) -> str:
    key = f"{path}\x1f{start}\x1f{end}\x1f{replacement}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]
