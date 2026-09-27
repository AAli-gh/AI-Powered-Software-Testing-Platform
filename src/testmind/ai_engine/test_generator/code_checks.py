"""Static checks on LLM-written test code, before anything is executed."""

from __future__ import annotations

import ast
import re

from testmind.ai_engine.test_generator.models import SuspectedBug

FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL)
# Defence in depth only: the sandbox already has no network and a read-only filesystem.
FORBIDDEN_MODULES = frozenset(
    {"socket", "subprocess", "requests", "httpx", "urllib", "http.client", "ftplib", "smtplib",
     "ctypes", "multiprocessing"}
)  # fmt: skip
FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "compile", "__import__", "os.system", "os.popen", "os.remove", "os.unlink",
     "os.rmdir", "os.kill", "os.fork"}
)  # fmt: skip


def extract_code(response: str) -> str | None:
    """The last fenced Python block (or the whole reply when it is plain code)."""
    blocks = FENCE.findall(response)
    if blocks:
        return str(blocks[-1]).strip() + "\n"
    stripped = response.strip()
    return stripped + "\n" if stripped.startswith(("import ", "from ", "def ", "@")) else None


def check_code(code: str, target_module: str | None) -> list[str]:
    """Problems that make the code unacceptable; empty when it may be executed.

    `target_module` must be imported by the tests (None: no such requirement)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]

    problems: list[str] = []
    if not any(
        isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test")
        for n in ast.walk(tree)
    ):
        problems.append("No test functions (def test_...) were found")

    imported = _imported_modules(tree)
    if target_module is not None and not any(
        m == target_module or m.startswith(target_module + ".") for m in imported
    ):
        problems.append(f"The module under test ({target_module}) is never imported")
    for module in sorted(imported):
        if module.split(".")[0] in FORBIDDEN_MODULES or module in FORBIDDEN_MODULES:
            problems.append(f"Forbidden import: {module} (tests must not use network/processes)")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in FORBIDDEN_CALLS:
                problems.append(f"Forbidden call: {name}() (line {node.lineno})")
    return problems


def count_tests(code: str) -> int:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0
    return sum(
        1
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name.startswith("test")
    )


def suspected_bugs(code: str) -> list[SuspectedBug]:
    """Tests the model marked `@pytest.mark.xfail(reason="possible bug: ...")`."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    bugs: list[SuspectedBug] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call) and _call_name(decorator.func).endswith("xfail")
            ):
                continue
            reason = next(
                (k.value.value for k in decorator.keywords if k.arg == "reason"
                 and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)),
                None,
            )  # fmt: skip
            if reason and reason.lower().startswith("possible bug"):
                bugs.append(SuspectedBug(test=node.name, reason=reason))
    return bugs


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _call_name(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))
