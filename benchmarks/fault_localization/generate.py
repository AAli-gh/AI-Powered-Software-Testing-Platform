"""Build labelled fault-localisation examples by injecting real bugs into a small corpus.

For each corpus project this takes every function, applies single-line source mutations to it,
and for each mutant that at least one test catches, records a labelled example: the program
spectrum (which tests executed which function, from coverage.py's per-test contexts), the
failing and passing tests, and the mutated function as the known fault.

Everything runs with pytest and coverage in a throwaway directory — no Docker, no network — so
the dataset is reproducible anywhere. The examples feed `train.py`; nothing here trains.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import coverage

CORPUS = Path(__file__).resolve().parent / "corpus"

# Single-line, length-preserving source swaps: each turns a correct line into a plausible bug.
MUTATIONS: tuple[tuple[str, str], ...] = (
    ("==", "!="),
    ("!=", "=="),
    (" < ", " <= "),
    (" > ", " >= "),
    (" <= ", " < "),
    (" >= ", " > "),
    (" + ", " - "),
    (" - ", " + "),
    (" * ", " + "),
    (" and ", " or "),
    (" or ", " and "),
    (" <= ", " > "),
    (" >= ", " < "),
)

# The conftest dropped into each run records every test's nodeid and outcome.
_CONFTEST = """
import json, os
_outcomes = {}
def pytest_runtest_logreport(report):
    rank = {"passed": 0, "skipped": 1, "failed": 2}
    current = _outcomes.get(report.nodeid, "passed")
    if rank.get(report.outcome, 0) >= rank.get(current, 0):
        _outcomes[report.nodeid] = report.outcome
def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["TM_OUTCOMES"], "w") as fh:
        json.dump(_outcomes, fh)
"""


@dataclass(frozen=True, slots=True)
class FunctionSpan:
    key: str  # 'relpath::qualname'
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Source:
    """One corpus source file and the project it belongs to."""

    project_dir: Path
    project: str
    module: str
    path: Path


@dataclass(slots=True)
class Example:
    project: str
    faulty_key: str
    tests: list[str]
    functions: dict[str, tuple[int, list[int]]]  # key -> (line, test indexes)
    failing: list[str]
    passing: list[str]

    def to_json(self) -> dict[str, object]:
        return {
            "project": self.project,
            "faulty_key": self.faulty_key,
            "tests": self.tests,
            "functions": {k: [line, idx] for k, (line, idx) in self.functions.items()},
            "failing": self.failing,
            "passing": self.passing,
        }


def _function_spans(source: str, relpath: str) -> list[FunctionSpan]:
    tree = ast.parse(source)
    spans: list[FunctionSpan] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                end = child.end_lineno or child.lineno
                spans.append(FunctionSpan(f"{relpath}::{prefix}{child.name}", child.lineno, end))

    visit(tree, "")
    return spans


def _mutants(lines: list[str], span: FunctionSpan) -> list[list[str]]:
    """Each distinct single-line mutation inside the span, as a new list of lines."""
    out: list[list[str]] = []
    seen: set[tuple[int, str]] = set()
    for i in range(span.start, span.end + 1):  # 1-based, inclusive
        line = lines[i - 1]
        for old, new in MUTATIONS:
            if old in line:
                mutated = line.replace(old, new, 1)
                key = (i, mutated)
                if mutated != line and key not in seen:
                    seen.add(key)
                    copy = list(lines)
                    copy[i - 1] = mutated
                    out.append(copy)
    return out


def _run(tree: Path, module: str) -> tuple[dict[str, str], coverage.CoverageData] | None:
    outcomes_path = tree / "outcomes.json"
    (tree / "conftest.py").write_text(_CONFTEST, "utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(tree),
        "TM_OUTCOMES": str(outcomes_path),
    }
    argv = [
        sys.executable,
        "-m",
        "pytest",
        f"--cov={module}",
        "--cov-context=test",
        "--cov-report=",
        "-p",
        "no:cacheprovider",
        "-q",
    ]
    # Fixed argv (no shell, no user input) run against a throwaway corpus tree.
    subprocess.run(argv, cwd=tree, env=env, capture_output=True, timeout=120, check=False)  # noqa: S603
    if not outcomes_path.exists():
        return None  # the run never started (collection error): not a usable example
    outcomes = json.loads(outcomes_path.read_text("utf-8"))
    data = coverage.CoverageData(basename=str(tree / ".coverage"))
    data.read()
    return outcomes, data


def _spectrum(
    tree: Path, data: coverage.CoverageData, spans: list[FunctionSpan]
) -> tuple[list[str], dict[str, tuple[int, list[int]]]]:
    by_file: dict[str, dict[int, list[str]]] = {}
    tests: set[str] = set()
    for measured in data.measured_files():
        rel = str(Path(measured).resolve().relative_to(tree.resolve()))
        contexts = {
            line: [c.split("|", 1)[0] for c in ctxs if c]
            for line, ctxs in data.contexts_by_lineno(measured).items()
        }
        by_file[rel] = contexts
        for ctxs in contexts.values():
            tests.update(ctxs)
    order = sorted(t for t in tests if t)
    index = {t: i for i, t in enumerate(order)}
    functions: dict[str, tuple[int, list[int]]] = {}
    for span in spans:
        rel = span.key.split("::", 1)[0]
        ran: set[int] = set()
        for line, ctxs in by_file.get(rel, {}).items():
            if span.start <= line <= span.end:
                ran.update(index[c] for c in ctxs if c in index)
        if ran:
            functions[span.key] = (span.start, sorted(ran))
    return order, functions


@dataclass(slots=True)
class Stats:
    mutants: int = 0
    killed: int = 0
    examples: list[Example] = field(default_factory=list)


def generate(corpus: Path = CORPUS) -> Stats:
    stats = Stats()
    for project_dir in sorted(p for p in corpus.iterdir() if p.is_dir()):
        project = project_dir.name
        sources = [p for p in project_dir.glob("*.py") if not p.name.startswith("test_")]
        for source_path in sources:
            source = Source(project_dir, project, source_path.stem, source_path)
            text = source_path.read_text("utf-8")
            lines = text.splitlines()
            spans = _function_spans(text, source_path.name)
            for span in spans:
                for mutant_lines in _mutants(lines, span):
                    stats.mutants += 1
                    example = _example(source, span, mutant_lines)
                    if example is not None:
                        stats.killed += 1
                        stats.examples.append(example)
    return stats


def _example(source: Source, span: FunctionSpan, mutant_lines: list[str]) -> Example | None:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        for path in source.project_dir.glob("*.py"):
            (tree / path.name).write_text(path.read_text("utf-8"), "utf-8")
        (tree / source.path.name).write_text("\n".join(mutant_lines) + "\n", "utf-8")
        result = _run(tree, source.module)
        if result is None:
            return None
        outcomes, data = result
        failing = sorted(n for n, o in outcomes.items() if o in {"failed", "error"})
        if not failing:
            return None  # the mutant survived: not a labelled failure
        passing = sorted(n for n, o in outcomes.items() if o == "passed")
        tests, functions = _spectrum(tree, data, _spans_of(tree))
    return Example(source.project, span.key, tests, functions, failing, passing)


def _spans_of(tree: Path) -> list[FunctionSpan]:
    spans: list[FunctionSpan] = []
    for path in tree.glob("*.py"):
        if path.name.startswith(("test_", "conftest")):
            continue
        spans.extend(_function_spans(path.read_text("utf-8"), path.name))
    return spans


def write_jsonl(stats: Stats, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for example in stats.examples:
            fh.write(json.dumps(example.to_json()) + "\n")
