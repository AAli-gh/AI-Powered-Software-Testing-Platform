"""What a fix changed: files, lines of the buggy version, and the functions around them.

Computed from the unified diff and Python's `ast`, independently of TestMind. Function names
follow Python's `__qualname__` (PEP 3155): `Class.method`, `outer.<locals>.inner`.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
MODULE = "<module>"


@dataclass(frozen=True, slots=True)
class Location:
    path: str
    qualname: str  # MODULE for lines outside any function


@dataclass(slots=True)
class GroundTruth:
    lines: dict[str, set[int]] = field(default_factory=dict)  # buggy-version lines per file
    functions: set[Location] = field(default_factory=set)

    @property
    def files(self) -> set[str]:
        return set(self.lines)


def changed_lines(patch: str) -> dict[str, set[int]]:
    """Per Python file, the lines of the old (buggy) version a diff removes or changes; a pure
    insertion counts as touching the old line it follows."""
    result: dict[str, set[int]] = {}
    path: str | None = None
    old = 0
    for line in patch.splitlines():
        if line.startswith("--- "):
            name = line[4:].split("\t", 1)[0].strip()
            path = name[2:] if name.startswith("a/") else None
            continue
        if line.startswith("+++ "):
            continue
        match = HUNK.match(line)
        if match:
            old = int(match.group(1))
            continue
        if path is None or not path.endswith(".py") or not line:
            continue
        lines = result.setdefault(path, set())
        if line[0] == "-":
            lines.add(old)
            old += 1
        elif line[0] == "+":
            lines.add(max(old - 1, 1))
        elif line[0] == " ":
            old += 1
    return {p: lines for p, lines in result.items() if lines}


def enclosing_functions(source: str, lines: set[int]) -> dict[int, str]:
    """For each line, the qualname of the innermost function containing it, or MODULE."""
    spans: list[tuple[int, int, str]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{prefix}{child.name}"
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                spans.append((start, child.end_lineno or child.lineno, name))
                visit(child, f"{name}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(ast.parse(source), "")
    result = {}
    for line in lines:
        inside = [(start, name) for start, end, name in spans if start <= line <= end]
        result[line] = max(inside)[1] if inside else MODULE
    return result


def ground_truth(patch: str, read: dict[str, str]) -> GroundTruth:
    """`read`: the buggy version of each changed file (missing or unparsable: lines only)."""
    truth = GroundTruth(lines=changed_lines(patch))
    for path, lines in truth.lines.items():
        source = read.get(path)
        if source is None:
            continue
        try:
            functions = enclosing_functions(source, lines)
        except SyntaxError:
            continue
        truth.functions.update(Location(path, name) for name in functions.values())
    return truth
