"""Which tests a change can affect. Deterministic, and every selected test says why.

Rules, in order:
1. A dependency manifest, test configuration, `conftest.py` or the Django settings module
   changed: everything is affected.
2. A test file was added or modified: that file runs.
3. A function that existed at base was modified or removed: the tests that executed it at base
   run (per-test coverage contexts). Without per-test coverage, rule 4 applies instead.
4. New code (added functions or files), module-level changes and removed modules: the test
   files that import the module, directly or through other modules of the repository.
Changes that no test reaches are reported as notes, not hidden.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from testmind.analyzers.code.models import CodeProfile, ImportRef
from testmind.analyzers.code.symbols import collect_imports
from testmind.analyzers.repository.entry_points import module_name
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.coverage_engine.impact import TestImpact
from testmind.coverage_engine.report import is_production
from testmind.regression.diff import FunctionStatus, ModuleDiff
from testmind.regression.models import ChangedFile, SelectedTarget, Selection
from testmind.vcs import FileChange

GLOBAL_FILES = frozenset(
    {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "Pipfile.lock", "poetry.lock",
        "uv.lock", "pytest.ini", "tox.ini", "conftest.py",
    }
)  # fmt: skip


def classify(change: FileChange, head: RepositoryProfile, base: RepositoryProfile) -> ChangedFile:
    name = PurePosixPath(change.path).name
    if (
        name in GLOBAL_FILES
        or (name.startswith("requirements") and name.endswith(".txt"))
        or change.path in _settings_paths(head)
    ):
        kind = "configuration"
    elif change.path in head.test_files or change.path in base.test_files:
        kind = "test"
    elif change.path.endswith(".py"):
        kind = "production" if is_production(change.path) else "test"
    else:
        kind = "other"
    return ChangedFile(path=change.path, status=change.status.value, kind=kind)


@dataclass(slots=True)
class _Targets:
    reasons: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    def add(self, target: str, reason: str) -> None:
        self.reasons[target].append(reason)


def select_tests(
    files: list[ChangedFile],
    diffs: dict[str, ModuleDiff],
    *,
    head: RepositoryProfile,
    head_code: CodeProfile,
    read_head: Callable[[str], str | None],
    base_impact: TestImpact | None,
) -> Selection:
    """`read_head` returns a file's source at head (for the imports of test files)."""
    targets = _Targets()
    notes: list[str] = []
    head_tests = set(head.test_files)

    global_changes = [f.path for f in files if f.kind == "configuration"]
    if global_changes:
        reason = "configuration or dependencies changed: " + ", ".join(global_changes)
        for test_file in sorted(head_tests):
            targets.add(test_file, reason)
        return Selection(everything=True, targets=_sorted(targets), notes=notes)

    for changed in files:
        if changed.kind == "test" and changed.path in head_tests:
            targets.add(changed.path, f"test file {changed.status}")

    imports = _ImportGraph(head_code, {t: read_head(t) for t in sorted(head_tests)})
    for path, diff in sorted(diffs.items()):
        _select_for_module(
            path, diff, targets=targets, notes=notes, head_tests=head_tests, imports=imports,
            base_impact=base_impact,
        )  # fmt: skip
    if base_impact is None and diffs:
        notes.append("No per-test coverage at base: tests were selected through imports only")
    return Selection(everything=False, targets=_sorted(targets), notes=notes)


def _select_for_module(
    path: str,
    diff: ModuleDiff,
    *,
    targets: _Targets,
    notes: list[str],
    head_tests: set[str],
    imports: _ImportGraph,
    base_impact: TestImpact | None,
) -> None:
    module = module_name(path)
    if diff.parse_error is not None:
        notes.append(diff.parse_error)
    # New code, module-level code, unparsable versions and missing per-test coverage are
    # reached through imports; existing functions through the tests that executed them.
    by_import = diff.parse_error is not None or diff.module_level_changed
    for function in diff.functions:
        key = TestImpact.key(path, function.qualname)
        if function.status is FunctionStatus.ADDED or base_impact is None:
            by_import = True
            continue
        covering = base_impact.functions.get(key, [])
        for test in covering:
            if test.partition("::")[0] in head_tests:
                targets.add(test, f"executed {function.qualname} ({function.status.value}) at base")
        if not covering:
            notes.append(f"{key} ({function.status.value}) was not executed by any test at base")
    if by_import:
        importers = imports.test_files_importing(module)
        for test_file in importers:
            targets.add(test_file, f"imports {module} (changed)")
        if not importers:
            notes.append(f"No test imports {module}, directly or indirectly")


def is_selected(selection: Selection, nodeid: str) -> bool:
    """`nodeid` as TestMind reports it (`file::name`); targets are files or pytest nodeids."""
    file = nodeid.partition("::")[0]
    for target in selection.targets:
        if target.target == file or short_nodeid(target.target) == nodeid:
            return True
    return False


def pytest_targets(selection: Selection) -> tuple[str, ...]:
    """Arguments for pytest. Given a file and a nodeid inside it, pytest collects only the
    nodeid, so nodeids whose whole file is selected are dropped."""
    files = {t.target for t in selection.targets if "::" not in t.target}
    return tuple(
        t.target
        for t in selection.targets
        if "::" not in t.target or t.target.partition("::")[0] not in files
    )


def short_nodeid(nodeid: str) -> str:
    """'tests/t.py::TestA::test_b[1]' → 'tests/t.py::test_b[1]' (TestMind's reported form)."""
    parts = nodeid.split("::")
    return f"{parts[0]}::{parts[-1]}" if len(parts) > 1 else nodeid


class _ImportGraph:
    def __init__(self, code: CodeProfile, test_sources: dict[str, str | None]) -> None:
        self._modules = {m.module: m.imports for m in code.modules}
        self._tests: dict[str, list[ImportRef]] = {}
        for path, source in test_sources.items():
            if source is None:
                continue
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError):
                continue
            is_package = PurePosixPath(path).name == "__init__.py"
            self._tests[path] = collect_imports(tree, module_name(path), is_package=is_package)

    def test_files_importing(self, module: str) -> list[str]:
        affected = self._dependents(module)
        return sorted(
            path for path, refs in self._tests.items() if any(_hits(r, affected) for r in refs)
        )

    def _dependents(self, module: str) -> set[str]:
        """The module and every repository module that imports it, transitively."""
        affected = {module}
        changed = True
        while changed:
            changed = False
            for name, refs in self._modules.items():
                if name not in affected and any(_hits(r, affected) for r in refs):
                    affected.add(name)
                    changed = True
        return affected


def _hits(ref: ImportRef, modules: Iterable[str]) -> bool:
    """Does this import load one of `modules`? Importing a package's submodule also runs the
    package itself, and `from pkg import mod` imports `pkg.mod`."""
    imported = {ref.module, *(f"{ref.module}.{name}" for name in ref.names)}
    return any(
        name == module or name.startswith(module + ".") for name in imported for module in modules
    )


def _settings_paths(profile: RepositoryProfile) -> set[str]:
    if not profile.django_settings_module:
        return set()
    relative = profile.django_settings_module.replace(".", "/") + ".py"
    return {relative, "src/" + relative}


def _sorted(targets: _Targets) -> list[SelectedTarget]:
    return [
        SelectedTarget(target=target, reasons=reasons)
        for target, reasons in sorted(targets.reasons.items())
    ]
