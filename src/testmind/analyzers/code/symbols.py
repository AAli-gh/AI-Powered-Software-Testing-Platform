"""Functions, classes and imports extracted from a module's AST."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from testmind.analyzers.code.complexity import (
    FunctionNode,
    complexity_rank,
    cyclomatic_complexity,
    iter_own_nodes,
)
from testmind.analyzers.code.models import (
    ClassInfo,
    FunctionInfo,
    ImportRef,
    Parameter,
    ParameterKind,
)

MAX_SOURCE_TEXT = 200


def source_text(node: ast.AST) -> str:
    text = ast.unparse(node)
    return text if len(text) <= MAX_SOURCE_TEXT else text[: MAX_SOURCE_TEXT - 1] + "…"


def first_line(docstring: str | None) -> str | None:
    if not docstring:
        return None
    line = docstring.strip().splitlines()[0].strip()
    return line[:MAX_SOURCE_TEXT] or None


def is_private_name(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def parameters_of(function: FunctionNode) -> list[Parameter]:
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults += list(args.defaults)

    result: list[Parameter] = []
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        kind = (
            ParameterKind.POSITIONAL_ONLY
            if index < len(args.posonlyargs)
            else ParameterKind.POSITIONAL_OR_KEYWORD
        )
        result.append(_parameter(arg, kind, default))
    if args.vararg:
        result.append(_parameter(args.vararg, ParameterKind.VAR_POSITIONAL, None))
    for arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        result.append(_parameter(arg, ParameterKind.KEYWORD_ONLY, kw_default))
    if args.kwarg:
        result.append(_parameter(args.kwarg, ParameterKind.VAR_KEYWORD, None))
    return result


def _parameter(arg: ast.arg, kind: ParameterKind, default: ast.expr | None) -> Parameter:
    return Parameter(
        name=arg.arg,
        kind=kind,
        annotation=source_text(arg.annotation) if arg.annotation else None,
        default=source_text(default) if default is not None else None,
    )


def raised_names(function: FunctionNode) -> list[str]:
    names: list[str] = []
    for node in iter_own_nodes(function):
        if not isinstance(node, ast.Raise):
            continue
        if node.exc is None:
            name = "<re-raise>"
        else:
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            name = source_text(target)
        if name not in names:
            names.append(name)
    return names


@dataclass(slots=True)
class _Scope:
    kind: str  # "class" | "function"
    name: str


@dataclass(slots=True)
class SymbolTable:
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    # (node, qualname) pairs: the rules use them to name the enclosing symbol of a finding.
    function_nodes: list[tuple[FunctionNode, str]] = field(default_factory=list)


class SymbolCollector(ast.NodeVisitor):
    def __init__(self, path: str, module: str) -> None:
        self._path = path
        self._module = module
        self._scopes: list[_Scope] = []
        self.table = SymbolTable()

    def _qualname(self, name: str) -> str:
        parts: list[str] = []
        for scope in self._scopes:
            parts.append(scope.name)
            if scope.kind == "function":
                parts.append("<locals>")
        return ".".join([*parts, name])

    def _is_public(self, name: str) -> bool:
        if any(scope.kind == "function" for scope in self._scopes):
            return False
        return not any(is_private_name(n) for n in [*(s.name for s in self._scopes), name])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.table.classes.append(
            ClassInfo(
                qualname=self._qualname(node.name),
                name=node.name,
                path=self._path,
                module=self._module,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                bases=[source_text(b) for b in node.bases],
                decorators=[source_text(d) for d in node.decorator_list],
                docstring=first_line(ast.get_docstring(node)),
                methods=[
                    s.name
                    for s in node.body
                    if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)
                ],
                is_public=self._is_public(node.name),
            )
        )
        self._scopes.append(_Scope("class", node.name))
        self.generic_visit(node)
        self._scopes.pop()

    def _visit_function(self, node: FunctionNode) -> None:
        complexity = cyclomatic_complexity(node)
        end_line = node.end_lineno or node.lineno
        qualname = self._qualname(node.name)
        self.table.functions.append(
            FunctionInfo(
                qualname=qualname,
                name=node.name,
                path=self._path,
                module=self._module,
                line=node.lineno,
                end_line=end_line,
                lines=end_line - node.lineno + 1,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                is_method=bool(self._scopes) and self._scopes[-1].kind == "class",
                is_public=self._is_public(node.name),
                decorators=[source_text(d) for d in node.decorator_list],
                parameters=parameters_of(node),
                returns=source_text(node.returns) if node.returns else None,
                docstring=first_line(ast.get_docstring(node)),
                complexity=complexity,
                complexity_rank=complexity_rank(complexity),
                raises=raised_names(node),
            )
        )
        self.table.function_nodes.append((node, qualname))
        self._scopes.append(_Scope("function", node.name))
        self.generic_visit(node)
        self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def collect_symbols(tree: ast.Module, path: str, module: str) -> SymbolTable:
    collector = SymbolCollector(path, module)
    collector.visit(tree)
    return collector.table


def collect_imports(tree: ast.Module, module: str, *, is_package: bool) -> list[ImportRef]:
    """Every import in the module, with relative imports resolved to absolute names."""
    package_parts = module.split(".") if module else []
    if not is_package:
        package_parts = package_parts[:-1]

    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend(
                ImportRef(module=alias.name, names=[], line=node.lineno, relative=False)
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            if node.level == 0:
                target = node.module or ""
            else:
                keep = len(package_parts) - (node.level - 1)
                if keep < 0:
                    continue  # beyond the top-level package: cannot be resolved
                target = ".".join([*package_parts[:keep], *([node.module] if node.module else [])])
            refs.append(
                ImportRef(module=target, names=names, line=node.lineno, relative=node.level > 0)
            )
    return sorted(refs, key=lambda r: (r.line, r.module))
