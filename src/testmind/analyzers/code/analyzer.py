"""Code analysis of a repository's Python modules. Parses with `ast`; never imports or runs code."""

from __future__ import annotations

import ast
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from testmind.analyzers.code.models import (
    ClassInfo,
    CodeMetrics,
    CodeProfile,
    Finding,
    FunctionInfo,
    ModuleInfo,
)
from testmind.analyzers.code.rules import RuleContext, analyse_module, import_aliases
from testmind.analyzers.code.symbols import collect_imports, collect_symbols, first_line
from testmind.analyzers.repository.entry_points import module_name
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.models import Issue
from testmind.analyzers.repository.walker import read_relative
from testmind.api_engine.discovery import discover

# Bump when the output changes meaningfully; stored with every analysis.
CODE_ANALYZER_VERSION = "1.1.0"  # 1.1: API endpoint discovery


class CodeAnalyzer:
    def __init__(self, limits: AnalysisLimits | None = None) -> None:
        self._limits = limits or AnalysisLimits()

    def analyze(
        self, root: Path, python_files: Iterable[str], test_files: Iterable[str] = ()
    ) -> CodeProfile:
        """Analyse `python_files` (paths relative to `root`); `test_files` are counted only."""
        tests = set(test_files)
        modules: list[ModuleInfo] = []
        functions: list[FunctionInfo] = []
        classes: list[ClassInfo] = []
        findings: list[Finding] = []
        issues: list[Issue] = []
        skipped_tests = 0
        parsed: list[tuple[str, ast.Module]] = []

        for path in sorted(set(python_files)):
            if path in tests:
                skipped_tests += 1
                continue
            source = read_relative(root, path, self._limits)
            if source is None:
                issues.append(Issue(path=path, message="Not analysed (too large or unreadable)"))
                continue
            try:
                tree = ast.parse(source, filename=path)
            except (SyntaxError, ValueError) as exc:
                issues.append(Issue(path=path, message=f"Syntax error: {exc}"))
                continue

            parsed.append((path, tree))
            module = module_name(path)
            is_package = PurePosixPath(path).name == "__init__.py"
            table = collect_symbols(tree, path, module)
            lines = source_lines(source)
            modules.append(
                ModuleInfo(
                    path=path,
                    module=module,
                    lines=len(lines),
                    docstring=first_line(ast.get_docstring(tree)),
                    imports=collect_imports(tree, module, is_package=is_package),
                    functions=len(table.functions),
                    classes=len(table.classes),
                )
            )
            functions.extend(table.functions)
            classes.extend(table.classes)
            findings.extend(
                analyse_module(
                    RuleContext(
                        path=path,
                        tree=tree,
                        lines=lines,
                        function_nodes=table.function_nodes,
                        aliases=import_aliases(tree),
                        is_package_init=is_package,
                    )
                )
            )

        return CodeProfile(
            metrics=_metrics(modules, functions, classes, findings, skipped_tests),
            modules=modules,
            functions=functions,
            classes=classes,
            findings=sorted(findings, key=lambda f: (f.path, f.line, f.rule_id)),
            issues=issues,
            api=discover(parsed),
        )


def source_lines(source: str) -> list[str]:
    """Physical lines as Python numbers them.

    str.splitlines() also breaks on \x0c, \x1c, \u2028, ..., which would shift every later
    line number relative to the AST.
    """
    lines = re.split(r"\r\n|\r|\n", source)
    return lines[:-1] if lines and lines[-1] == "" else lines


def _metrics(
    modules: list[ModuleInfo],
    functions: list[FunctionInfo],
    classes: list[ClassInfo],
    findings: list[Finding],
    skipped_tests: int,
) -> CodeMetrics:
    complexities = [f.complexity for f in functions]
    return CodeMetrics(
        modules=len(modules),
        test_modules_skipped=skipped_tests,
        lines=sum(m.lines for m in modules),
        functions=sum(1 for f in functions if not f.is_method),
        methods=sum(1 for f in functions if f.is_method),
        classes=len(classes),
        average_complexity=round(sum(complexities) / len(complexities), 2) if complexities else 0.0,
        max_complexity=max(complexities, default=0),
        complexity_ranks=dict(sorted(Counter(f.complexity_rank for f in functions).items())),
        findings_by_severity=dict(sorted(Counter(f.severity.value for f in findings).items())),
        findings_by_category=dict(sorted(Counter(f.category.value for f in findings).items())),
    )
