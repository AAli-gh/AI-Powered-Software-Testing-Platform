"""Cyclomatic complexity (McCabe) computed directly on the AST.

complexity = 1 + number of decision points in the function's own body:
  if / elif, for, while, except handler, conditional expression, each extra operand of
  `and` / `or`, each `for` and `if` inside a comprehension, each `case` of a match statement.
Nested functions, lambdas and classes are measured separately and do not add to their parent.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

DECISION_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.IfExp,
    ast.ExceptHandler,
    ast.match_case,
)
RANKS = (("A", 5), ("B", 10), ("C", 20), ("D", 30), ("E", 40))


NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def iter_own_nodes(function: FunctionNode) -> Iterator[ast.AST]:
    """All nodes of a function body in source order, without entering nested scopes.

    A nested function/class/lambda node itself is yielded, but nothing inside it.
    """
    stack: list[ast.AST] = list(reversed(function.body))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, NESTED_SCOPES):
            stack.extend(reversed(list(ast.iter_child_nodes(node))))


def cyclomatic_complexity(function: FunctionNode) -> int:
    complexity = 1
    for node in iter_own_nodes(function):
        if isinstance(node, DECISION_NODES):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            complexity += 1 + len(node.ifs)
    return complexity


def complexity_rank(complexity: int) -> str:
    for rank, upper in RANKS:
        if complexity <= upper:
            return rank
    return "F"
