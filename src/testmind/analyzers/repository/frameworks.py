"""Framework and test-framework detection with explicit, weighted evidence.

A detection is reported only when at least one concrete fact supports it; its confidence is the
(capped) sum of the weights of the facts found. No single weak signal reaches high confidence.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from testmind.analyzers.repository.models import Dependency, Detection, DetectionCategory
from testmind.analyzers.repository.python_scanner import PythonFileFacts

W_DEPENDENCY = 0.5
W_IMPORT = 0.3
W_STRUCTURE = 0.4
MIN_CONFIDENCE = 0.3
EXAMPLES_IN_EVIDENCE = 3

PYTEST_CONFIG_FILES = frozenset({"pytest.ini", "conftest.py"})


@dataclass(slots=True)
class _Score:
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)

    def add(self, weight: float, fact: str) -> None:
        self.confidence = min(1.0, self.confidence + weight)
        self.evidence.append(fact)


@dataclass(frozen=True, slots=True)
class FrameworkInputs:
    dependencies: list[Dependency]
    python_files: list[PythonFileFacts]
    test_files: set[str]
    all_paths: list[str]
    pytest_config_sections: list[str]  # e.g. "pyproject.toml [tool.pytest.ini_options]"


def is_test_file(facts: PythonFileFacts) -> bool:
    path = PurePosixPath(facts.path)
    name = path.name
    if name in {"conftest.py", "__init__.py"}:
        return False
    if name.startswith("test_") or name.endswith("_test.py") or name == "tests.py":
        return True
    in_test_dir = any(part in {"test", "tests"} for part in path.parts[:-1])
    return in_test_dir and bool(facts.testcase_classes or facts.pytest_style_tests)


def detect_frameworks(inputs: FrameworkInputs) -> tuple[list[Detection], list[Detection]]:
    web = [
        detection
        for detection in (
            _web_framework(inputs, "Django", "django", _django_structure(inputs)),
            _web_framework(inputs, "FastAPI", "fastapi", _app_structure(inputs, "fastapi")),
            _web_framework(inputs, "Flask", "flask", _app_structure(inputs, "flask")),
        )
        if detection is not None
    ]
    tests = [d for d in (_pytest(inputs), _unittest(inputs), _playwright(inputs)) if d is not None]
    return _sorted(web), _sorted(tests)


def _sorted(detections: list[Detection]) -> list[Detection]:
    return sorted(detections, key=lambda d: (-d.confidence, d.name))


def _finish(name: str, category: DetectionCategory, score: _Score) -> Detection | None:
    if score.confidence < MIN_CONFIDENCE:
        return None
    return Detection(
        name=name,
        category=category,
        confidence=round(score.confidence, 2),
        evidence=score.evidence,
    )


def _examples(paths: Iterable[str]) -> str:
    items = sorted(paths)
    shown = ", ".join(items[:EXAMPLES_IN_EVIDENCE])
    more = len(items) - EXAMPLES_IN_EVIDENCE
    return shown + (f" (+{more} more)" if more > 0 else "")


def _dependency_fact(inputs: FrameworkInputs, package: str, score: _Score) -> None:
    sources = sorted({d.source for d in inputs.dependencies if d.name == package})
    if sources:
        score.add(W_DEPENDENCY, f"'{package}' declared in {_examples(sources)}")


def _import_fact(inputs: FrameworkInputs, module: str, score: _Score) -> None:
    importers = [f.path for f in inputs.python_files if module in f.imports]
    if importers:
        score.add(
            W_IMPORT, f"'{module}' imported in {len(importers)} file(s): {_examples(importers)}"
        )


def _web_framework(
    inputs: FrameworkInputs, name: str, module: str, structure: list[str]
) -> Detection | None:
    score = _Score()
    _dependency_fact(inputs, module, score)
    _import_fact(inputs, module, score)
    if structure:
        score.add(W_STRUCTURE, "; ".join(structure))
    return _finish(name, DetectionCategory.WEB_FRAMEWORK, score)


def _django_structure(inputs: FrameworkInputs) -> list[str]:
    facts: list[str] = []
    configured = [f.path for f in inputs.python_files if f.sets_django_settings_module]
    if configured:
        facts.append(f"DJANGO_SETTINGS_MODULE set in {_examples(configured)}")
    settings = [f.path for f in inputs.python_files if f.defines_installed_apps]
    if settings:
        facts.append(f"INSTALLED_APPS defined in {_examples(settings)}")
    apps = [
        f"{a.framework.split('_')[1].upper()} application in {f.path}"
        for f in inputs.python_files
        for a in f.app_instances
        if a.framework.startswith("django_")
    ]
    return facts + apps


def _app_structure(inputs: FrameworkInputs, framework: str) -> list[str]:
    return [
        f"{framework} app instance in {f.path}:{a.line}"
        for f in inputs.python_files
        for a in f.app_instances
        if a.framework == framework
    ]


def _pytest(inputs: FrameworkInputs) -> Detection | None:
    score = _Score()
    _dependency_fact(inputs, "pytest", score)
    _import_fact(inputs, "pytest", score)
    config = [p for p in inputs.all_paths if PurePosixPath(p).name in PYTEST_CONFIG_FILES]
    config += inputs.pytest_config_sections
    if config:
        score.add(W_STRUCTURE, f"pytest configuration: {_examples(config)}")
    functions = sum(
        f.pytest_style_tests for f in inputs.python_files if f.path in inputs.test_files
    )
    if functions:
        score.add(W_IMPORT, f"{functions} module-level test function(s) (pytest style)")
    return _finish("pytest", DetectionCategory.TEST_FRAMEWORK, score)


def _unittest(inputs: FrameworkInputs) -> Detection | None:
    score = _Score()
    _import_fact(inputs, "unittest", score)
    classes = [f for f in inputs.python_files if f.path in inputs.test_files and f.testcase_classes]
    if classes:
        count = sum(len(f.testcase_classes) for f in classes)
        score.add(
            W_STRUCTURE + W_IMPORT,
            f"{count} TestCase subclass(es) in {_examples(f.path for f in classes)}",
        )
    return _finish("unittest", DetectionCategory.TEST_FRAMEWORK, score)


PLAYWRIGHT = "Playwright"


def _playwright(inputs: FrameworkInputs) -> Detection | None:
    """Browser tests: they need the browser sandbox image (docs/api-testing.md)."""
    score = _Score()
    _dependency_fact(inputs, "pytest-playwright", score)
    _dependency_fact(inputs, "playwright", score)
    importers = [
        f.path
        for f in inputs.python_files
        if f.path in inputs.test_files and "playwright" in f.imports
    ]
    if importers:
        score.add(
            W_STRUCTURE, f"'playwright' imported in {len(importers)} test file(s): "
            f"{_examples(importers)}",
        )  # fmt: skip
    return _finish(PLAYWRIGHT, DetectionCategory.TEST_FRAMEWORK, score)
