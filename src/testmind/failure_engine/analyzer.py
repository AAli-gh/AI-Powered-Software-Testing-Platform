"""Deterministic failure analysis: categorise, group, link and collect evidence. No LLM.

Input: the plugin's failure records, the execution's test results, the run's analyses and the
execution's coverage. Output: groups of failures that share a cause, with the facts about
them. An LLM may later explain a group (failure_engine.explainer), but only from these facts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.coverage_engine import CoverageReport
from testmind.coverage_engine.report import is_production
from testmind.failure_engine.capture import RawCapture, RawFailure, parse_collection_error
from testmind.failure_engine.models import (
    Blame,
    Evidence,
    FailedTest,
    FailureCategory,
    FailureGroup,
    FailureReport,
    Frame,
    FrameLocation,
    LinkedFunction,
)
from testmind.sandbox.models import Outcome
from testmind.sandbox.paths import report_path
from testmind.test_engine.models import TestCaseReport

IMPORT_ERRORS = frozenset({"ModuleNotFoundError", "ImportError"})
SYNTAX_ERRORS = frozenset({"SyntaxError", "IndentationError", "TabError"})
FIXTURE_PHASES = frozenset({"setup", "teardown"})
MISSING_MODULE = re.compile(r"No module named '([^']+)'")
MAX_EVIDENCE_TEXT = 1000
NO_CAPTURE = "No structured failure data was recorded (the run timed out or pytest crashed)"


@dataclass(slots=True)
class _Group:
    category: FailureCategory
    first: RawFailure
    crash: Frame | None
    raised_at: Frame | None
    frames: list[Frame]
    exception_type: str | None
    message: str
    tests: list[FailedTest] = field(default_factory=list)
    missing_module: str | None = None


class FailureAnalyzer:
    def __init__(
        self,
        repository: RepositoryProfile,
        code: CodeProfile | None = None,
        coverage: CoverageReport | None = None,
    ) -> None:
        self._tests = set(repository.test_files)
        self._repository = repository
        self._code = code
        self._coverage = coverage

    def analyze(self, capture: RawCapture | None, tests: Iterable[TestCaseReport]) -> FailureReport:
        failing = [t for t in tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}]
        notes: list[str] = []
        if capture is None:
            if failing:
                notes.append(NO_CAPTURE)
            return FailureReport(
                groups=[],
                failed_tests=len(failing),
                analysed_tests=0,
                unanalysed_tests=[t.nodeid for t in failing],
                notes=notes,
            )
        if capture.dropped:
            notes.append(f"{capture.dropped} further failures were not recorded (limit reached)")

        groups: dict[str, _Group] = {}
        for failure in capture.failures:
            key, group, test = self._classify(failure, capture.rootdir)
            groups.setdefault(key, group).tests.append(test)

        analysed = {(t.file, t.name) for g in groups.values() for t in g.tests}
        collected_files = {t.file for g in groups.values() for t in g.tests if t.name is None}
        unanalysed = [
            t.nodeid
            for t in failing
            if (t.file, t.name) not in analysed and t.file not in collected_files
        ]
        built = [self._build(key, group) for key, group in groups.items()]
        built.sort(key=lambda g: (-len(g.tests), g.category.value, g.id))
        return FailureReport(
            groups=built,
            failed_tests=len(failing),
            analysed_tests=sum(len(g.tests) for g in built),
            unanalysed_tests=unanalysed,
            notes=notes,
        )

    # -------------------------------------------------------------------------- classification
    def _classify(self, failure: RawFailure, rootdir: str) -> tuple[str, _Group, FailedTest]:
        if failure.phase == "collect":
            return self._collection(failure, rootdir)
        located = report_path(failure.path or "", rootdir)
        file, generated = located if located else (failure.path or "?", False)
        test = FailedTest(
            nodeid=f"{file}::{failure.name}",
            file=file,
            name=failure.name,
            line=failure.line,
            phase=failure.phase,
            generated=generated,
        )
        if failure.unexpected_pass:
            group = _Group(
                category=FailureCategory.UNEXPECTED_PASS,
                first=failure,
                crash=None,
                raised_at=None,
                frames=[],
                exception_type=None,
                message=(failure.longrepr or "").strip(),
            )
            return _key(group.category, file, failure.name), group, test

        frames = [self._frame(f.path, f.line, f.function, f.code) for f in failure.frames]
        crash = next(
            (f for f in reversed(frames) if f.location is not FrameLocation.EXTERNAL), None
        )
        raised_at = frames[-1] if frames else None
        exception = failure.exception
        exception_type = exception.type if exception else None
        message = exception.message if exception else ""
        category = self._category(failure.phase, exception_type, crash, raised_at)
        group = _Group(
            category=category,
            first=failure,
            crash=crash,
            raised_at=raised_at,
            frames=frames,
            exception_type=exception_type,
            message=message,
        )
        if category is FailureCategory.IMPORT_ERROR:
            group.missing_module = _missing_module(message)
            return _key(category, exception_type, group.missing_module or message), group, test
        if category is FailureCategory.ASSERTION_FAILURE:
            return _key(category, *_site(crash)), group, test
        return _key(category, exception_type, *_site(crash)), group, test

    def _collection(self, failure: RawFailure, rootdir: str) -> tuple[str, _Group, FailedTest]:
        located = report_path(failure.nodeid or "", rootdir)
        file, generated = located if located else (failure.nodeid or "?", False)
        test = FailedTest(
            nodeid=file, file=file, name=None, line=None, phase="collect", generated=generated
        )
        parsed = parse_collection_error(failure.longrepr or "")
        crash = None
        if parsed.path is not None and parsed.line is not None:
            crash = self._frame(parsed.path, parsed.line, "<module>", "", rootdir=rootdir)
        if parsed.exception_type in SYNTAX_ERRORS:
            category = FailureCategory.SYNTAX_ERROR
        elif parsed.exception_type in IMPORT_ERRORS:
            category = FailureCategory.IMPORT_ERROR
        else:
            category = FailureCategory.COLLECTION_ERROR
        group = _Group(
            category=category,
            first=failure,
            crash=crash,
            raised_at=crash,
            frames=[crash] if crash else [],
            exception_type=parsed.exception_type,
            message=parsed.message,
        )
        if category is FailureCategory.IMPORT_ERROR:
            group.missing_module = _missing_module(parsed.message)
            return _key(category, parsed.exception_type, group.missing_module or file), group, test
        return _key(category, parsed.exception_type, *_site(crash), file), group, test

    @staticmethod
    def _category(
        phase: str, exception_type: str | None, crash: Frame | None, raised_at: Frame | None
    ) -> FailureCategory:
        if exception_type in IMPORT_ERRORS:
            return FailureCategory.IMPORT_ERROR
        if phase in FIXTURE_PHASES:
            return FailureCategory.FIXTURE_ERROR
        if raised_at is not None and raised_at.location is FrameLocation.EXTERNAL:
            return FailureCategory.EXCEPTION_IN_LIBRARY
        if crash is not None and crash.location is FrameLocation.PRODUCTION:
            return FailureCategory.EXCEPTION_IN_CODE
        if exception_type == "AssertionError":
            return FailureCategory.ASSERTION_FAILURE
        return FailureCategory.EXCEPTION_IN_TEST

    def _frame(
        self, path: str, line: int, function: str, code: str, *, rootdir: str = "/"
    ) -> Frame:
        located = report_path(path, rootdir)
        if located is None:
            return Frame(
                path=path, line=line, function=function, code=code,
                location=FrameLocation.EXTERNAL,
            )  # fmt: skip
        relative, generated = located
        if generated:
            location = FrameLocation.GENERATED
        elif relative in self._tests or not is_production(relative):
            location = FrameLocation.TEST
        else:
            location = FrameLocation.PRODUCTION
        return Frame(path=relative, line=line, function=function, code=code, location=location)

    # -------------------------------------------------------------------------- linking
    def _build(self, key: str, group: _Group) -> FailureGroup:
        function = self._function(group.crash)
        blame, reason = self._blame(group)
        return FailureGroup(
            id=key,
            category=group.category,
            exception_type=group.exception_type,
            message=group.message,
            crash=group.crash,
            raised_at=group.raised_at,
            frames=group.frames,
            locals=dict(group.first.locals),
            chain=[f"{c.type}: {c.message}" for c in group.first.chain],
            tests=group.tests,
            function=function,
            evidence=self._evidence(group, function),
            blame=blame,
            blame_reason=reason,
        )

    def _function(self, crash: Frame | None) -> LinkedFunction | None:
        if crash is None or crash.location is not FrameLocation.PRODUCTION or self._code is None:
            return None
        info = _innermost(self._code.functions, crash.path, crash.line)
        if info is None:
            return None
        findings = sorted(
            {
                f.rule_id
                for f in self._code.findings
                if f.path == info.path and info.line <= f.line <= info.end_line
            }
        )
        percent = None
        if self._coverage is not None:
            percent = next(
                (
                    c.percent
                    for c in self._coverage.functions
                    if c.path == info.path and c.qualname == info.qualname
                ),
                None,
            )
        return LinkedFunction(
            path=info.path,
            qualname=info.qualname,
            line=info.line,
            end_line=info.end_line,
            complexity=info.complexity,
            findings=findings,
            coverage_percent=percent,
        )

    def _evidence(self, group: _Group, function: LinkedFunction | None) -> list[Evidence]:
        evidence: list[Evidence] = []
        crash, raised_at = group.crash, group.raised_at
        if group.category is FailureCategory.EXCEPTION_IN_LIBRARY and raised_at and crash:
            evidence.append(
                Evidence(
                    kind="traceback",
                    detail=(
                        f"{group.exception_type} raised inside {raised_at.path} "
                        f"({raised_at.function}), called from {crash.path}:{crash.line}: "
                        f"{crash.code}"
                    ),
                    path=crash.path,
                    line=crash.line,
                )
            )
        elif crash is not None:
            where = f"{crash.path}:{crash.line}"
            code = f": {crash.code}" if crash.code else ""
            evidence.append(
                Evidence(
                    kind="traceback",
                    detail=(
                        f"{group.exception_type or 'Failure'} at {where} in {crash.function}{code}"
                    ),
                    path=crash.path,
                    line=crash.line,
                )
            )
        if group.category is FailureCategory.ASSERTION_FAILURE and group.message:
            evidence.append(Evidence(kind="assertion", detail=_clip(group.message)))
        if group.first.locals and crash is not None:
            values = ", ".join(f"{k} = {v}" for k, v in group.first.locals.items())
            evidence.append(
                Evidence(
                    kind="locals",
                    detail=_clip(f"Local variables in {crash.function}: {values}"),
                    path=crash.path,
                    line=crash.line,
                )
            )
        evidence += [
            Evidence(kind="chain", detail=_clip(f"Caused by {c.type}: {c.message}"))
            for c in group.first.chain
        ]
        evidence += self._finding_evidence(crash, function)
        if group.missing_module is not None:
            evidence.append(self._dependency_evidence(group.missing_module))
        if len(group.tests) > 1:
            generated = sum(1 for t in group.tests if t.generated)
            suffix = f" ({generated} of them generated)" if generated else ""
            evidence.append(
                Evidence(
                    kind="repetition",
                    detail=f"{len(group.tests)} tests fail the same way at the same place{suffix}",
                )
            )
        return evidence

    def _finding_evidence(
        self, crash: Frame | None, function: LinkedFunction | None
    ) -> list[Evidence]:
        if crash is None or self._code is None:
            return []
        evidence = []
        for finding in self._code.findings:
            if finding.path != crash.path:
                continue
            if finding.line == crash.line:
                detail = (
                    f"Static analysis flagged the failing line: {finding.rule_id} "
                    f"({finding.severity.value}, confidence {finding.confidence}): "
                    f"{finding.message}"
                )
            elif function is not None and function.line <= finding.line <= function.end_line:
                detail = (
                    f"Static analysis flagged line {finding.line} of {function.qualname}: "
                    f"{finding.rule_id}: {finding.message}"
                )
            else:
                continue
            evidence.append(
                Evidence(kind="finding", detail=detail, path=finding.path, line=finding.line)
            )
        return evidence

    def _dependency_evidence(self, module: str) -> Evidence:
        top = module.split(".", maxsplit=1)[0]
        if self._is_repository_module(top):
            detail = f"'{top}' is a package of the repository, so '{module}' should exist in it"
        elif (declared := self._declared(top)) is not None:
            detail = (
                f"'{top}' is not part of the repository; a dependency named '{declared.name}' "
                f"is declared in {declared.source} but could not be imported"
            )
        else:
            detail = (
                f"'{top}' is not part of the repository and no dependency with that name is "
                "declared (the distribution may have a different name)"
            )
        return Evidence(kind="dependency", detail=detail)

    def _blame(self, group: _Group) -> tuple[Blame | None, str | None]:
        """Only where the facts decide. Everything else is left to the (optional) AI step."""
        if group.category is FailureCategory.UNEXPECTED_PASS:
            return Blame.TEST, (
                "The test is marked xfail(strict=True) but passed: the failure it documents no "
                "longer happens, so the marker is out of date"
            )
        if group.category is FailureCategory.SYNTAX_ERROR and group.crash is not None:
            if group.crash.location is FrameLocation.PRODUCTION:
                return Blame.CODE, f"{group.crash.path} is production code that does not parse"
            if group.crash.location is not FrameLocation.EXTERNAL:
                return Blame.TEST, f"{group.crash.path} is test code that does not parse"
        if (
            group.category is FailureCategory.IMPORT_ERROR
            and group.exception_type == "ModuleNotFoundError"
            and group.missing_module is not None
            and not self._is_repository_module(group.missing_module.split(".")[0])
        ):
            return Blame.ENVIRONMENT, (
                f"No module named '{group.missing_module}': it is not part of the repository, "
                "so a dependency is missing or failed to install"
            )
        return None, None

    def _is_repository_module(self, top: str) -> bool:
        if self._code is not None and any(
            m.module.split(".")[0] == top for m in self._code.modules
        ):
            return True
        # Test packages and namespace packages without analysed modules
        return any(
            f.path.split("/")[0] == top or f.path.startswith(("src/" + top + "/", top + ".py"))
            for f in self._repository.files
        )

    def _declared(self, top: str) -> _Declared | None:
        wanted = _normalise(top)
        for dependency in self._repository.dependencies:
            if _normalise(dependency.name) == wanted:
                return _Declared(dependency.name, dependency.source)
        return None


@dataclass(frozen=True, slots=True)
class _Declared:
    name: str
    source: str


def _innermost(functions: Iterable[FunctionInfo], path: str, line: int) -> FunctionInfo | None:
    enclosing = [f for f in functions if f.path == path and f.line <= line <= f.end_line]
    return max(enclosing, key=lambda f: f.line, default=None)


def _missing_module(message: str) -> str | None:
    match = MISSING_MODULE.search(message)
    return match.group(1) if match else None


def _site(frame: Frame | None) -> tuple[str, str]:
    return (frame.path, str(frame.line)) if frame else ("", "")


def _key(*parts: object) -> str:
    text = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _clip(text: str) -> str:
    return text if len(text) <= MAX_EVIDENCE_TEXT else text[:MAX_EVIDENCE_TEXT] + "…"
