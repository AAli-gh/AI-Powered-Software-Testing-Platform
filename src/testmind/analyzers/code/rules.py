"""Deterministic findings (AST rules).

Every rule has a stable id, a category, a severity and a confidence. Rules that are heuristics
(missing validation / error handling) say so through a confidence of 0.5 or less. A finding is
suppressed by a `# testmind: ignore` or `# testmind: ignore[TM010,...]` comment on its line.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass

from testmind.analyzers.code.complexity import FunctionNode, cyclomatic_complexity, iter_own_nodes
from testmind.analyzers.code.models import Finding, FindingCategory, Severity
from testmind.analyzers.code.symbols import is_private_name

COMPLEXITY_MEDIUM = 10
COMPLEXITY_HIGH = 20

BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
PICKLE_LOADERS = frozenset(
    {
        "pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads", "marshal.load",
        "marshal.loads", "dill.load", "dill.loads", "shelve.open", "jsonpickle.decode",
    }
)  # fmt: skip
SHELL_FUNCTIONS = frozenset({"os.system", "os.popen"})
REQUESTS_METHODS = frozenset(
    {
        f"requests.{m}"
        for m in ("get", "post", "put", "patch", "delete", "head", "options", "request")
    }
)
SAFE_YAML_LOADERS = frozenset({"SafeLoader", "CSafeLoader", "BaseLoader", "CBaseLoader"})
CONVERSIONS = frozenset(
    {"int", "float", "decimal.Decimal", "json.loads", "uuid.UUID", "datetime.fromisoformat",
     "datetime.datetime.fromisoformat", "date.fromisoformat", "datetime.date.fromisoformat"}
)  # fmt: skip
CONVERSION_ERRORS = frozenset(
    {"ValueError", "TypeError", "Exception", "BaseException", "JSONDecodeError",
     "json.JSONDecodeError", "decimal.InvalidOperation", "InvalidOperation", "ArithmeticError"}
)  # fmt: skip
DIVISION_ERRORS = frozenset({"ZeroDivisionError", "ArithmeticError", "Exception", "BaseException"})
SECRET_NAME = re.compile(r"(password|passwd|secret|api_?key|private_?key|token)$", re.IGNORECASE)
MIN_SECRET_LENGTH = 8
IGNORE_COMMENT = re.compile(r"#\s*testmind:\s*ignore(?:\[([A-Z0-9, ]+)\])?")
# Equivalent flake8/ruff codes: a `# noqa` for them also silences our rule.
NOQA_EQUIVALENTS = {"TM023": "F401", "TM001": "E722", "TM020": "B006", "TM010": "S307"}
NOQA_COMMENT = re.compile(r"#\s*noqa(?::\s*([A-Z0-9, ]+))?", re.IGNORECASE)
MAX_EVIDENCE = 200


@dataclass(frozen=True, slots=True)
class Rule:
    category: FindingCategory
    title: str


C = FindingCategory
RULES: dict[str, Rule] = {
    "TM001": Rule(C.ERROR_HANDLING, "Bare except"),
    "TM002": Rule(C.ERROR_HANDLING, "Exception silently swallowed"),
    "TM003": Rule(C.ERROR_HANDLING, "Broad exception handler without re-raise"),
    "TM010": Rule(C.DANGEROUS_PATTERN, "eval / exec"),
    "TM011": Rule(C.DANGEROUS_PATTERN, "Unsafe deserialisation (pickle, marshal, ...)"),
    "TM012": Rule(C.DANGEROUS_PATTERN, "Shell command execution"),
    "TM013": Rule(C.DANGEROUS_PATTERN, "yaml.load without a safe Loader"),
    "TM014": Rule(C.DANGEROUS_PATTERN, "Hard-coded secret"),
    "TM015": Rule(C.POTENTIAL_BUG, "assert used in production code"),
    "TM016": Rule(C.POTENTIAL_BUG, "HTTP request without timeout"),
    "TM017": Rule(C.DANGEROUS_PATTERN, "Insecure temporary file"),
    "TM020": Rule(C.POTENTIAL_BUG, "Mutable default argument"),
    "TM021": Rule(C.POTENTIAL_BUG, "Identity comparison with a literal"),
    "TM022": Rule(C.DEAD_CODE, "Unreachable code"),
    "TM023": Rule(C.DEAD_CODE, "Unused import"),
    "TM024": Rule(C.DEAD_CODE, "Unused private function"),
    "TM030": Rule(C.COMPLEXITY, "High cyclomatic complexity"),
    "TM040": Rule(C.MISSING_VALIDATION, "Division by an unvalidated parameter"),
    "TM041": Rule(C.MISSING_VALIDATION, "Conversion of a parameter without error handling"),
}


@dataclass(frozen=True, slots=True)
class RuleContext:
    path: str
    tree: ast.Module
    lines: list[str]  # split exactly like Python's tokenizer (see analyzer.source_lines)
    function_nodes: list[tuple[FunctionNode, str]]
    aliases: dict[str, str]  # bound name -> fully qualified import target
    is_package_init: bool


def analyse_module(context: RuleContext) -> list[Finding]:
    findings = [
        *_call_rules(context),
        *_error_handling_rules(context),
        *_potential_bug_rules(context),
        *_dead_code_rules(context),
        *_function_rules(context),
        *_secret_rules(context),
    ]
    unique = {(f.rule_id, f.line, f.message): f for f in findings if not _suppressed(context, f)}
    return sorted(unique.values(), key=lambda f: (f.line, f.rule_id))


# ---------------------------------------------------------------------------------- helpers
def import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def dotted_name(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def resolve(context: RuleContext, node: ast.expr) -> str | None:
    """Fully qualified name of an expression, following import aliases."""
    name = dotted_name(node)
    if name is None:
        return None
    head, _, rest = name.partition(".")
    target = context.aliases.get(head)
    if target is None:
        return name
    return f"{target}.{rest}" if rest else target


def _make(
    context: RuleContext,
    node: ast.AST,
    rule_id: str,
    severity: Severity,
    message: str,
    *,
    confidence: float = 1.0,
) -> Finding:
    line = getattr(node, "lineno", 1)
    text = context.lines[line - 1].strip()
    return Finding(
        rule_id=rule_id,
        category=RULES[rule_id].category,
        severity=severity,
        confidence=confidence,
        path=context.path,
        line=line,
        symbol=_enclosing_symbol(context, line),
        message=message,
        evidence=text[:MAX_EVIDENCE],
    )


def _enclosing_symbol(context: RuleContext, line: int) -> str | None:
    best: tuple[int, str] | None = None
    for node, qualname in context.function_nodes:
        start, end = node.lineno, node.end_lineno or node.lineno
        if start <= line <= end and (best is None or start >= best[0]):
            best = (start, qualname)
    return best[1] if best else None


def _suppressed(context: RuleContext, finding: Finding) -> bool:
    line = context.lines[finding.line - 1]
    match = IGNORE_COMMENT.search(line)
    if match is not None:
        rules = match.group(1)
        return rules is None or finding.rule_id in {r.strip() for r in rules.split(",")}
    equivalent = NOQA_EQUIVALENTS.get(finding.rule_id)
    noqa = NOQA_COMMENT.search(line) if equivalent else None
    if noqa is None:
        return False
    codes = noqa.group(1)
    return codes is None or equivalent in {c.strip().upper() for c in codes.split(",")}


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in call.keywords if k.arg == name), None)


def _has_kwargs_splat(call: ast.Call) -> bool:
    return any(k.arg is None for k in call.keywords)


# ---------------------------------------------------------------------------------- calls
def _call_rules(context: RuleContext) -> Iterator[Finding]:
    params_by_line = _parameter_names_by_function(context)
    for node in ast.walk(context.tree):
        if not isinstance(node, ast.Call):
            continue
        name = resolve(context, node.func)
        if name in {"eval", "exec"} and name not in context.aliases:
            yield from _eval_exec(context, node, name, params_by_line)
        elif name in PICKLE_LOADERS:
            yield _make(
                context, node, "TM011", Severity.HIGH,
                f"{name}() can execute arbitrary code when given untrusted data",
            )  # fmt: skip
        elif name in SHELL_FUNCTIONS:
            yield _make(
                context, node, "TM012", Severity.HIGH,
                f"{name}() runs a shell command (command injection risk); use subprocess with "
                "an argument list",
            )  # fmt: skip
        elif name and name.startswith("subprocess.") and _is_true(_keyword(node, "shell")):
            yield _make(
                context, node, "TM012", Severity.HIGH,
                f"{name}(shell=True) enables command injection; pass an argument list instead",
            )  # fmt: skip
        elif name in {"yaml.load", "yaml.load_all", "yaml.unsafe_load"} and not _safe_yaml(node):
            yield _make(
                context, node, "TM013", Severity.HIGH,
                f"{name}() without a safe Loader can construct arbitrary Python objects; "
                "use yaml.safe_load()",
            )  # fmt: skip
        elif (
            name in REQUESTS_METHODS
            and _keyword(node, "timeout") is None
            and not _has_kwargs_splat(node)
        ):
            yield _make(
                context, node, "TM016", Severity.MEDIUM,
                f"{name}() without a timeout can hang forever",
            )  # fmt: skip
        elif name == "tempfile.mktemp":
            yield _make(
                context, node, "TM017", Severity.MEDIUM,
                "tempfile.mktemp() is race-prone; use NamedTemporaryFile or mkstemp",
            )  # fmt: skip


def _eval_exec(
    context: RuleContext,
    node: ast.Call,
    name: str,
    params_by_line: dict[int, set[str]],
) -> Iterator[Finding]:
    argument = node.args[0] if node.args else None
    params = params_by_line.get(node.lineno, set())
    from_parameter = isinstance(argument, ast.Name) and argument.id in params
    yield _make(
        context, node, "TM010",
        Severity.CRITICAL if from_parameter else Severity.HIGH,
        f"{name}() executes arbitrary code"
        + (" built from a function argument" if from_parameter else ""),
    )  # fmt: skip


def _is_true(keyword: ast.keyword | None) -> bool:
    return (
        keyword is not None
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
    )


def _safe_yaml(call: ast.Call) -> bool:
    if dotted_name(call.func) == "yaml.unsafe_load":
        return False
    loader = _keyword(call, "Loader")
    candidate = loader.value if loader else (call.args[1] if len(call.args) > 1 else None)
    if candidate is None:
        return False
    name = dotted_name(candidate) or ""
    return name.split(".")[-1] in SAFE_YAML_LOADERS


def _parameter_names_by_function(context: RuleContext) -> dict[int, set[str]]:
    """line -> parameter names of the innermost function containing that line."""
    by_line: dict[int, tuple[int, set[str]]] = {}
    for node, _ in context.function_nodes:
        names = {a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)}
        start, end = node.lineno, node.end_lineno or node.lineno
        # function_nodes is in pre-order, so an inner function always overwrites its parent.
        for line in range(start, end + 1):
            by_line[line] = (start, names)
    return {line: names for line, (_, names) in by_line.items()}


# ---------------------------------------------------------------------------------- errors
def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names: set[str] = set()
    for t in types:
        name = dotted_name(t)
        if name:
            names.add(name)
            names.add(name.split(".")[-1])
    return names


def _only_pass(body: list[ast.stmt]) -> bool:
    def empty(stmt: ast.stmt) -> bool:
        # `continue` / `return` are deliberate control flow, not swallowing.
        if isinstance(stmt, ast.Pass):
            return True
        return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)

    return all(empty(s) for s in body)


def _reraises(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(n, ast.Raise) for s in handler.body for n in ast.walk(s))


LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)


def _logs(handler: ast.ExceptHandler) -> bool:
    """The handler reports the error (logger.exception(...), log.warning(...), ...)."""
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in LOG_METHODS
        for s in handler.body
        for n in ast.walk(s)
    )


def _error_handling_rules(context: RuleContext) -> Iterator[Finding]:
    for node in ast.walk(context.tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        broad = bool(_handler_names(node) & BROAD_EXCEPTIONS)
        if node.type is None:
            yield _make(
                context, node, "TM001", Severity.MEDIUM,
                "Bare 'except:' also catches KeyboardInterrupt and SystemExit",
            )  # fmt: skip
        if _only_pass(node.body):
            yield _make(
                context, node, "TM002",
                Severity.HIGH if broad else Severity.LOW,
                "Exception is silently swallowed"
                + (" (broad catch hides unrelated bugs)" if broad else ""),
            )  # fmt: skip
        elif broad and node.type is not None and not _reraises(node) and not _logs(node):
            yield _make(
                context, node, "TM003", Severity.LOW,
                "Broad exception handler without re-raise may hide unexpected errors",
                confidence=0.5,
            )  # fmt: skip


# ---------------------------------------------------------------------------------- bugs
MUTABLE_CALLS = frozenset({"list", "dict", "set", "collections.defaultdict", "defaultdict"})


def _potential_bug_rules(context: RuleContext) -> Iterator[Finding]:
    for function, _ in context.function_nodes:
        defaults = [
            *function.args.defaults,
            *(d for d in function.args.kw_defaults if d is not None),
        ]
        for default in defaults:
            mutable = isinstance(default, ast.List | ast.Dict | ast.Set) or (
                isinstance(default, ast.Call) and dotted_name(default.func) in MUTABLE_CALLS
            )
            if mutable:
                yield _make(
                    context, default, "TM020", Severity.MEDIUM,
                    f"Mutable default argument in {function.name}() is shared between calls",
                )  # fmt: skip
    for node in ast.walk(context.tree):
        if isinstance(node, ast.Assert):
            yield _make(
                context, node, "TM015", Severity.LOW,
                "assert is stripped when Python runs with -O; do not use it for validation",
                confidence=0.7,
            )  # fmt: skip
        if isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, ast.Is | ast.IsNot) and _is_value_literal(right):
                    yield _make(
                        context, node, "TM021", Severity.MEDIUM,
                        "Identity comparison with a literal; use == / != instead of is / is not",
                    )  # fmt: skip


def _is_value_literal(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return not (node.value is None or isinstance(node.value, bool) or node.value is ...)
    return isinstance(node, ast.List | ast.Dict | ast.Set | ast.Tuple | ast.JoinedStr)


# ---------------------------------------------------------------------------------- dead code
TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _dead_code_rules(context: RuleContext) -> Iterator[Finding]:
    yield from _unreachable(context)
    yield from _unused_imports(context)
    yield from _unused_private_functions(context)


def _unreachable(context: RuleContext) -> Iterator[Finding]:
    for node in ast.walk(context.tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, stmt in enumerate(block[:-1]):
                if isinstance(stmt, TERMINATORS):
                    yield _make(
                        context, block[index + 1], "TM022",
                        Severity.MEDIUM,
                        f"Unreachable code after '{type(stmt).__name__.lower()}'",
                    )  # fmt: skip
                    break


def _used_names(tree: ast.Module) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.update(_names_in_string(node.value))
    return used


def _names_in_string(value: str) -> set[str]:
    """Names inside string annotations ('Optional[Foo]') and `__all__` entries."""
    if len(value) > MAX_EVIDENCE or not value.strip():
        return set()
    try:
        expression = ast.parse(value.strip(), mode="eval")
    except SyntaxError:
        return set()
    return {n.id for n in ast.walk(expression) if isinstance(n, ast.Name)}


def _unused_imports(context: RuleContext) -> Iterator[Finding]:
    if context.is_package_init:
        return  # __init__.py imports are usually re-exports
    used = _used_names(context.tree)
    for stmt in context.tree.body:  # module-level only; try/if-guarded imports are skipped
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            continue
        if not isinstance(stmt, ast.Import | ast.ImportFrom):
            continue
        for alias in stmt.names:
            if alias.name == "*" or (alias.asname and alias.asname == alias.name):
                continue  # `import x as x` is the explicit re-export idiom
            bound = alias.asname or alias.name.split(".")[0]
            if bound not in used:
                yield _make(
                    context, stmt, "TM023", Severity.LOW,
                    f"'{alias.name}' is imported but never used",
                    confidence=0.9,
                )  # fmt: skip


def _unused_private_functions(context: RuleContext) -> Iterator[Finding]:
    used = _used_names(context.tree)
    for stmt in context.tree.body:
        if (
            isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
            and is_private_name(stmt.name)
            and not stmt.decorator_list  # decorators often register the function elsewhere
            and stmt.name not in used
        ):
            yield _make(
                context, stmt, "TM024", Severity.LOW,
                f"Private function {stmt.name}() is never used in its module",
                confidence=0.6,
            )  # fmt: skip


# ---------------------------------------------------------------------------------- functions
def _function_rules(context: RuleContext) -> Iterator[Finding]:
    for node, qualname in context.function_nodes:
        complexity = cyclomatic_complexity(node)
        if complexity > COMPLEXITY_MEDIUM:
            yield _make(
                context, node, "TM030",
                Severity.HIGH if complexity > COMPLEXITY_HIGH else Severity.MEDIUM,
                f"{qualname}() has cyclomatic complexity {complexity} "
                f"(> {COMPLEXITY_MEDIUM}); hard to test exhaustively",
            )  # fmt: skip
        yield from _validation_rules(context, node)


def _validation_rules(context: RuleContext, function: FunctionNode) -> Iterator[Finding]:
    arguments = (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)
    params = {a.arg for a in arguments} - {"self", "cls"}
    if not params:
        return
    non_numeric = {
        a.arg
        for a in arguments
        if a.annotation is not None and not _is_numeric_annotation(a.annotation)
    }
    checked = _names_in_conditions(function)
    guarded = _guarded_nodes(function)
    for node in iter_own_nodes(function):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div | ast.FloorDiv | ast.Mod)
            and isinstance(node.right, ast.Name)
            and node.right.id in params
            and node.right.id not in checked
            and node.right.id not in non_numeric
            and not (isinstance(node.left, ast.Name) and node.left.id in non_numeric)
            and not (
                isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant | ast.JoinedStr)
            )
            and id(node) not in guarded.get("division", set())
        ):
            yield _make(
                context, node, "TM040", Severity.MEDIUM,
                f"'{node.right.id}' is used as a divisor without being validated "
                "(possible ZeroDivisionError)",
                confidence=0.5,
            )  # fmt: skip
        elif (
            isinstance(node, ast.Call)
            and resolve(context, node.func) in CONVERSIONS
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in params
            and node.args[0].id not in checked
            and id(node) not in guarded.get("conversion", set())
        ):
            yield _make(
                context, node, "TM041", Severity.LOW,
                f"'{node.args[0].id}' is converted with {resolve(context, node.func)}() without "
                "error handling (invalid input raises)",
                confidence=0.4,
            )  # fmt: skip


NUMERIC_TYPES = frozenset({"int", "float", "complex", "Decimal", "Fraction", "Number", "Real"})


def _is_numeric_annotation(annotation: ast.expr) -> bool:
    """`int`, `float | None`, `Optional[Decimal]`, ... (anything else, e.g. `Path`, is not)."""
    names = {
        n.id if isinstance(n, ast.Name) else n.attr
        for n in ast.walk(annotation)
        if isinstance(n, ast.Name | ast.Attribute)
    }
    return bool(names & NUMERIC_TYPES)


def _names_in_conditions(function: FunctionNode) -> set[str]:
    """Parameter names that appear in any test: if/while/assert/ternary/comparison/isinstance."""
    names: set[str] = set()
    for node in iter_own_nodes(function):
        tests: list[ast.expr] = []
        if isinstance(node, ast.If | ast.While | ast.IfExp | ast.Assert):
            tests.append(node.test)
        elif isinstance(node, ast.Compare):
            tests.append(node)
        elif isinstance(node, ast.match_case) and node.guard is not None:
            tests.append(node.guard)
        for test in tests:
            names.update(n.id for n in ast.walk(test) if isinstance(n, ast.Name))
    return names


def _guarded_nodes(function: FunctionNode) -> dict[str, set[int]]:
    """ids of nodes inside a `try` whose handlers catch division / conversion errors."""
    guarded: dict[str, set[int]] = {"division": set(), "conversion": set()}
    for node in iter_own_nodes(function):
        if not isinstance(node, ast.Try | ast.TryStar):
            continue
        caught: set[str] = set()
        for handler in node.handlers:
            caught |= _handler_names(handler)
        inner = {id(n) for stmt in node.body for n in ast.walk(stmt)}
        if caught & DIVISION_ERRORS:
            guarded["division"] |= inner
        if caught & CONVERSION_ERRORS:
            guarded["conversion"] |= inner
    return guarded


# ---------------------------------------------------------------------------------- secrets
def _secret_rules(context: RuleContext) -> Iterator[Finding]:
    for node in ast.walk(context.tree):
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        if len(value.value) < MIN_SECRET_LENGTH or " " in value.value:
            continue
        for target in targets:
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
            if SECRET_NAME.search(name):
                yield _make(
                    context, node, "TM014", Severity.HIGH,
                    f"Possible hard-coded secret in '{name}'; load it from the environment",
                    confidence=0.6,
                )  # fmt: skip
