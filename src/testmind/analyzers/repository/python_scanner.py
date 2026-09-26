"""Facts extracted from Python source with `ast` (the code is parsed, never imported or run)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

# Call name → application kind. Django's are factory functions, the others are classes.
WEB_APP_CLASSES = {
    "FastAPI": "fastapi",
    "Flask": "flask",
    "Starlette": "starlette",
    "get_asgi_application": "django_asgi",
    "get_wsgi_application": "django_wsgi",
}
ASGI_KINDS = frozenset({"fastapi", "starlette", "django_asgi"})


@dataclass(slots=True)
class AppInstance:
    framework: str  # a value of WEB_APP_CLASSES
    variable: str | None  # module-level variable name, if assigned at module level
    line: int
    factory: str | None = None  # enclosing top-level function, e.g. Flask's `create_app`


@dataclass(slots=True)
class PythonFileFacts:
    path: str
    imports: set[str] = field(default_factory=set)  # top-level module names
    app_instances: list[AppInstance] = field(default_factory=list)
    has_main_guard: bool = False
    testcase_classes: list[str] = field(default_factory=list)
    pytest_style_tests: int = 0  # module-level `def test_*` functions
    sets_django_settings_module: bool = False  # os.environ[.setdefault]("DJANGO_SETTINGS_MODULE")
    defines_installed_apps: bool = False  # module-level INSTALLED_APPS = [...] (Django settings)
    django_settings_value: str | None = None  # "site.settings" when set to a string literal
    syntax_error: str | None = None


def scan_python(path: str, source: str) -> PythonFileFacts:
    facts = PythonFileFacts(path=path)
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:  # ValueError: source contains NUL bytes
        facts.syntax_error = str(exc)
        return facts

    for node in ast.walk(tree):
        if _sets_django_settings_module(node):
            facts.sets_django_settings_module = True
            facts.django_settings_value = facts.django_settings_value or _settings_value(node)
        if isinstance(node, ast.Import):
            facts.imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            facts.imports.add(node.module.split(".")[0])
        elif isinstance(node, ast.ClassDef) and any(_is_testcase_base(b) for b in node.bases):
            facts.testcase_classes.append(node.name)

    for stmt in tree.body:
        if _assigns_name(stmt, "INSTALLED_APPS"):
            facts.defines_installed_apps = True
        if isinstance(stmt, ast.If) and _is_main_guard(stmt.test):
            facts.has_main_guard = True
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name.startswith(
            "test"
        ):
            facts.pytest_style_tests += 1

    facts.app_instances = _find_app_instances(tree)
    return facts


DJANGO_SETTINGS_KEY = "DJANGO_SETTINGS_MODULE"


def _is_key(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == DJANGO_SETTINGS_KEY


def _sets_django_settings_module(node: ast.AST) -> bool:
    """True for `environ.setdefault("DJANGO_SETTINGS_MODULE", ...)` or an item assignment to it.

    Deliberately not a substring search: a file that merely *mentions* the name (a string in a
    detector, a comment) is not Django configuration.
    """
    if isinstance(node, ast.Call) and _called_class(node) == "setdefault" and node.args:
        return _is_key(node.args[0])
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Subscript) and _is_key(t.slice) for t in node.targets)
    return False


def _settings_value(node: ast.AST) -> str | None:
    value: ast.expr | None = None
    if isinstance(node, ast.Call) and len(node.args) > 1:
        value = node.args[1]
    elif isinstance(node, ast.Assign):
        value = node.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value
    return None


def _assigns_name(stmt: ast.stmt, name: str) -> bool:
    if isinstance(stmt, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets)
    if isinstance(stmt, ast.AnnAssign):
        return isinstance(stmt.target, ast.Name) and stmt.target.id == name
    return False


def _is_testcase_base(base: ast.expr) -> bool:
    # unittest.TestCase, django.test.TestCase, TransactionTestCase, IsolatedAsyncioTestCase, ...
    name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
    return isinstance(name, str) and name.endswith("TestCase")


def _is_main_guard(test: ast.expr) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1):
        return False
    left, right = test.left, test.comparators[0]
    sides = {_name_or_const(left), _name_or_const(right)}
    return sides == {"__name__", "'__main__'"} and isinstance(test.ops[0], ast.Eq)


def _name_or_const(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return repr(node.value)
    return None


def _called_class(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _find_app_instances(tree: ast.Module) -> list[AppInstance]:
    found: list[AppInstance] = []
    module_level: dict[int, str] = {}
    enclosing: dict[int, str] = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign | ast.AnnAssign) and isinstance(stmt.value, ast.Call):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if len(targets) == 1 and isinstance(targets[0], ast.Name):
                module_level[id(stmt.value)] = targets[0].id
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            for inner in ast.walk(stmt):
                enclosing[id(inner)] = stmt.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            cls = _called_class(node)
            if cls in WEB_APP_CLASSES:
                found.append(
                    AppInstance(
                        framework=WEB_APP_CLASSES[cls],
                        variable=module_level.get(id(node)),
                        line=node.lineno,
                        factory=enclosing.get(id(node)),
                    )
                )
    return found
