"""Deterministic choice of what to test: public, non-trivial production code first."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from testmind.analyzers.code.models import CodeProfile, Finding, FunctionInfo

EXCLUDED_FILES = frozenset(
    {"setup.py", "manage.py", "conftest.py", "__main__.py", "wsgi.py", "asgi.py"}
)
EXCLUDED_DIRS = frozenset({"migrations", "scripts", "docs", "examples"})
SKIPPED_DECORATORS = ("property", "setter", "abstractmethod", "overload")


@dataclass(frozen=True, slots=True)
class ModuleTargets:
    path: str
    module: str
    functions: list[FunctionInfo]
    findings: list[Finding]


def is_candidate(function: FunctionInfo) -> bool:
    path = PurePosixPath(function.path)
    if path.name in EXCLUDED_FILES or EXCLUDED_DIRS & set(path.parts[:-1]):
        return False
    if not function.is_public or function.name.startswith("__"):
        return False
    return not any(d.split(".")[-1] in SKIPPED_DECORATORS for d in function.decorators)


def select_targets(
    profile: CodeProfile, *, max_targets: int, path_prefix: str | None = None
) -> tuple[list[ModuleTargets], int]:
    """Return targets grouped per module, plus how many candidates existed in total.

    Priority: functions with findings, then higher complexity, then source order.
    """
    candidates = [
        f
        for f in profile.functions
        if is_candidate(f) and (not path_prefix or f.path.startswith(path_prefix))
    ]
    finding_counts = Counter((f.path, f.symbol) for f in profile.findings)
    ranked = sorted(
        candidates,
        key=lambda f: (-finding_counts[(f.path, f.qualname)], -f.complexity, f.path, f.line),
    )[:max_targets]

    by_path: dict[str, list[FunctionInfo]] = defaultdict(list)
    for function in ranked:
        by_path[function.path].append(function)

    groups = [
        ModuleTargets(
            path=path,
            module=functions[0].module,
            functions=sorted(functions, key=lambda f: f.line),
            findings=[f for f in profile.findings if f.path == path],
        )
        for path, functions in sorted(by_path.items())
    ]
    return groups, len(candidates)
