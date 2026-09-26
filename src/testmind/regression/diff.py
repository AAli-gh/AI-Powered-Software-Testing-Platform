"""What changed between two versions of a Python module, at function level.

Two versions of a function are compared by their normalised AST: `ast.dump` without positions,
with docstrings removed. Comments, blank lines, formatting and docstrings are therefore not
changes; any change to the code, its decorators or its signature is. A change inside a nested
function also changes the enclosing function (its body contains the nested definition), which
can only select more tests, never fewer.

Module-level code (imports, constants, class attributes, anything outside functions) is
compared the same way, with all function definitions removed.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from enum import StrEnum

from testmind.analyzers.code.symbols import collect_symbols
from testmind.analyzers.repository.entry_points import module_name


class FunctionStatus(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"


@dataclass(frozen=True, slots=True)
class FunctionChange:
    path: str
    qualname: str
    status: FunctionStatus
    base_lines: tuple[int, int] | None  # (first, last) in the base version
    head_lines: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class ModuleDiff:
    path: str
    functions: list[FunctionChange]
    module_level_changed: bool
    parse_error: str | None = None  # a version did not parse; treated as changed throughout


FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def diff_module(path: str, base: str | None, head: str | None) -> ModuleDiff:
    """`base` / `head` are the file's sources; None when the file does not exist there."""
    base_tree, base_error = _parse(base, path)
    head_tree, head_error = _parse(head, path)
    if base_error or head_error:
        return ModuleDiff(path, [], module_level_changed=True, parse_error=base_error or head_error)

    module = module_name(path)
    old = _functions(base_tree, path, module)
    new = _functions(head_tree, path, module)
    changes: list[FunctionChange] = []
    for qualname in sorted(old.keys() | new.keys()):
        before, after = old.get(qualname), new.get(qualname)
        if before is None and after is not None:
            status = FunctionStatus.ADDED
        elif after is None and before is not None:
            status = FunctionStatus.REMOVED
        elif before is not None and after is not None and before[1] != after[1]:
            status = FunctionStatus.MODIFIED
        else:
            continue
        changes.append(
            FunctionChange(
                path=path,
                qualname=qualname,
                status=status,
                base_lines=before[0] if before else None,
                head_lines=after[0] if after else None,
            )
        )
    module_changed = _module_fingerprint(base_tree) != _module_fingerprint(head_tree)
    return ModuleDiff(path, changes, module_level_changed=module_changed)


def _parse(source: str | None, path: str) -> tuple[ast.Module | None, str | None]:
    if source is None:
        return None, None
    try:
        return ast.parse(source, filename=path), None
    except (SyntaxError, ValueError) as exc:
        return None, f"{path} does not parse: {exc}"


def _functions(
    tree: ast.Module | None, path: str, module: str
) -> dict[str, tuple[tuple[int, int], str]]:
    """qualname -> ((first line, last line), fingerprint), with the code analyzer's qualnames."""
    if tree is None:
        return {}
    nodes = {
        (node.lineno, node.name): node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    functions = {}
    for info in collect_symbols(tree, path, module).functions:
        node = nodes[(info.line, info.name)]
        functions[info.qualname] = ((info.line, info.end_line), _fingerprint(node))
    return functions


def _fingerprint(node: ast.AST) -> str:
    clean = copy.deepcopy(node)
    for child in ast.walk(clean):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module):
            _drop_docstring(child)
    return ast.dump(clean, annotate_fields=True, include_attributes=False)


def _module_fingerprint(tree: ast.Module | None) -> str | None:
    if tree is None:
        return None
    clean = copy.deepcopy(tree)
    for child in ast.walk(clean):
        if isinstance(child, ast.Module | ast.ClassDef):
            child.body = [
                stmt
                for stmt in child.body
                if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            ] or [ast.Pass()]
    return _fingerprint(clean)


def _drop_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
) -> None:
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        node.body = body[1:] or [ast.Pass()]
