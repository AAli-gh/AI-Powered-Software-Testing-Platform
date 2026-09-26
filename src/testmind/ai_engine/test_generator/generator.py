"""Generate pytest modules with an LLM and validate them with a bounded fix loop.

Per module:  prompt → LLM → extract code → static checks → run in sandbox
             → on failure, send the concrete problems back to the LLM → … (max N iterations)

The LLM never decides whether a test is acceptable: static checks and a real pytest run do.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from testmind.ai_engine.llm import LLMError, LLMProvider, LLMRequest
from testmind.ai_engine.prompts import load_prompt
from testmind.ai_engine.test_generator.code_checks import (
    check_code,
    count_tests,
    extract_code,
    suspected_bugs,
)
from testmind.ai_engine.test_generator.models import (
    Attempt,
    FileStatus,
    GeneratedTestFile,
    GenerationOptions,
    GenerationResult,
    TestKind,
    TestRunSummary,
)
from testmind.ai_engine.test_generator.targets import ModuleTargets, select_targets
from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.walker import read_relative
from testmind.core.logging import get_logger
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError

logger = get_logger(__name__)

PROMPT_VERSION = 1
MAX_FEEDBACK = 6000
MAX_SOURCE_CHARS = 60_000
KIND_DESCRIPTIONS = {
    TestKind.UNIT: "- unit: normal, documented behaviour with representative inputs",
    TestKind.EDGE_CASES: (
        "- edge cases: zero, empty, None, boundaries, very large values where meaningful"
    ),
    TestKind.NEGATIVE: "- negative: invalid types/values and the errors the code should raise",
}
IMPORT_FAILURE = re.compile(r"(ModuleNotFoundError|ImportError): (.+)")


class TestSandbox(Protocol):
    """What the generator needs from a sandbox (DockerSandbox implements it)."""

    def available(self) -> bool: ...

    def run_pytest(
        self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
    ) -> SandboxResult: ...


@dataclass(slots=True)
class _Usage:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class _State:
    code: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    run: SandboxResult | None = None


class TestGenerator:
    __test__ = False

    def __init__(
        self,
        provider: LLMProvider,
        sandbox: TestSandbox | None,
        *,
        max_iterations: int,
        max_tokens: int,
        limits: AnalysisLimits | None = None,
        on_progress: Callable[[str], None] | None = None,
        image: str | None = None,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._image = image  # runner image with the project's dependencies, if built
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._limits = limits or AnalysisLimits()
        self._system = load_prompt("test_generation_system", PROMPT_VERSION)
        self._generate = load_prompt("generate_tests", PROMPT_VERSION)
        self._fix = load_prompt("fix_tests", PROMPT_VERSION)
        self._on_progress = on_progress or (lambda _: None)

    def generate(
        self, root: Path, profile: CodeProfile, options: GenerationOptions, *, max_targets: int
    ) -> GenerationResult:
        groups, available = select_targets(
            profile, max_targets=options.max_targets or max_targets, path_prefix=options.path_prefix
        )
        sandbox_ready = self._sandbox is not None and self._sandbox.available()
        usage = _Usage()
        files = [
            self._module(root, group, options, usage, sandbox_ready=sandbox_ready)
            for group in groups
        ]
        return GenerationResult(
            files=files,
            model=usage.model,
            prompt_version=f"v{PROMPT_VERSION}",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            targets_selected=sum(len(g.functions) for g in groups),
            targets_available=available,
        )

    # ------------------------------------------------------------------ one module
    def _module(
        self,
        root: Path,
        group: ModuleTargets,
        options: GenerationOptions,
        usage: _Usage,
        *,
        sandbox_ready: bool,
    ) -> GeneratedTestFile:
        self._on_progress(group.path)
        test_path = "tests/testmind/test_" + group.module.replace(".", "_") + ".py"
        state = _State()
        source = read_relative(root, group.path, self._limits) or ""

        prompt = self._generate.render(
            module=group.module,
            path=group.path,
            targets=_describe_targets(group.functions),
            kinds="\n".join(KIND_DESCRIPTIONS[k] for k in options.kinds),
            findings=_describe_findings(group) or "(none)",
            source=source[:MAX_SOURCE_CHARS],
        )
        status = FileStatus.REJECTED
        for iteration in range(1, self._max_iterations + 1):
            try:
                reply = self._ask(prompt, usage)
            except LLMError as exc:
                state.attempts.append(
                    Attempt(iteration=iteration, outcome="llm_error", feedback=str(exc))
                )
                status = FileStatus.LLM_ERROR
                break

            candidate = extract_code(reply)
            if candidate is None:
                problems = ["The reply did not contain a Python code block"]
            else:
                problems = check_code(candidate, group.module)
            if candidate is None or problems:
                feedback = "\n".join(f"- {p}" for p in problems)
                state.attempts.append(
                    Attempt(iteration=iteration, outcome="rejected", feedback=feedback)
                )
                status = FileStatus.REJECTED
            else:
                state.code = candidate
                status, feedback = self._execute(
                    root, test_path, candidate, state=state, iteration=iteration,
                    sandbox_ready=sandbox_ready,
                )  # fmt: skip
                if status is not FileStatus.FAILED:
                    break

            prompt = self._fix.render(
                module=group.module,
                feedback=feedback[:MAX_FEEDBACK],
                code=candidate or reply[:MAX_FEEDBACK],
            )

        run = state.run
        code = state.code  # last statically valid version, if any
        if status is FileStatus.REJECTED and run is not None:
            status = FileStatus.FAILED  # a valid version ran and failed; later rewrites were worse
        return GeneratedTestFile(
            path=test_path,
            module=group.module,
            targets=[f.qualname for f in group.functions],
            status=status,
            code=code,
            test_count=count_tests(code) if code else 0,
            iterations=len(state.attempts),
            run=_summary(run) if run else None,
            suspected_bugs=suspected_bugs(code) if code and status is FileStatus.PASSED else [],
            attempts=state.attempts,
        )

    def _ask(self, prompt: str, usage: _Usage) -> str:
        response = self._provider.complete(
            LLMRequest(system=self._system.render(), prompt=prompt, max_tokens=self._max_tokens)
        )
        usage.model = response.model
        usage.input_tokens += response.input_tokens
        usage.output_tokens += response.output_tokens
        return response.text

    def _execute(
        self,
        root: Path,
        test_path: str,
        code: str,
        *,
        state: _State,
        iteration: int,
        sandbox_ready: bool,
    ) -> tuple[FileStatus, str]:
        if not sandbox_ready or self._sandbox is None:
            state.attempts.append(Attempt(iteration=iteration, outcome="unverified"))
            return FileStatus.UNVERIFIED, ""
        try:
            result = self._sandbox.run_pytest(
                root, {test_path.removeprefix("tests/"): code}, image=self._image
            )
        except SandboxUnavailableError as exc:
            state.attempts.append(
                Attempt(iteration=iteration, outcome="unverified", feedback=str(exc))
            )
            return FileStatus.UNVERIFIED, ""
        state.run = result

        missing = _missing_third_party_module(result)
        if missing:
            feedback = f"The sandbox cannot import '{missing}' (dependency not installed)"
            state.attempts.append(
                Attempt(iteration=iteration, outcome="environment_error", feedback=feedback)
            )
            return FileStatus.ENVIRONMENT_ERROR, feedback

        report = _failure_report(result)
        if report is None:
            state.attempts.append(Attempt(iteration=iteration, outcome="passed"))
            return FileStatus.PASSED, ""
        state.attempts.append(Attempt(iteration=iteration, outcome="failed", feedback=report))
        return FileStatus.FAILED, report


# ---------------------------------------------------------------------------------- helpers
def _describe_targets(functions: list[FunctionInfo]) -> str:
    lines = []
    for f in functions:
        params = ", ".join(
            p.name
            + (f": {p.annotation}" if p.annotation else "")
            + (f" = {p.default}" if p.default else "")
            for p in f.parameters
        )
        facts = [f"complexity {f.complexity}"]
        if f.raises:
            facts.append("raises " + ", ".join(f.raises))
        if f.docstring:
            facts.append(f'doc: "{f.docstring}"')
        returns = f" -> {f.returns}" if f.returns else ""
        lines.append(f"- {f.qualname}({params}){returns}  [{'; '.join(facts)}] (line {f.line})")
    return "\n".join(lines)


def _describe_findings(group: ModuleTargets) -> str:
    return "\n".join(
        f"- line {f.line} {f.rule_id} ({f.severity.value}, confidence {f.confidence}): {f.message}"
        for f in group.findings
    )


def _summary(result: SandboxResult) -> TestRunSummary:
    return TestRunSummary(
        passed=result.count(Outcome.PASSED),
        failed=result.count(Outcome.FAILED),
        errors=result.count(Outcome.ERROR),
        xfailed=result.count(Outcome.XFAILED),
        skipped=result.count(Outcome.SKIPPED),
    )


def _missing_third_party_module(result: SandboxResult) -> str | None:
    """A dependency of the code under test is not installed in the sandbox (Phase 5 installs
    project dependencies). Retrying with the LLM cannot fix that, so it is reported instead."""
    text = result.output + "".join((t.details or "") + (t.message or "") for t in result.tests)
    match = IMPORT_FAILURE.search(text)
    if match is None:
        return None
    quoted = re.search(r"'([^']+)'", match.group(2))
    return quoted.group(1) if quoted else match.group(2).strip()


def _failure_report(result: SandboxResult) -> str | None:
    if result.timed_out:
        return "The test run timed out (infinite loop or very slow test?)."
    bad = [t for t in result.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}]
    if not bad and result.tests and result.exit_code in {0, 1}:
        return None
    if not result.tests:
        return "pytest collected no tests or crashed before running them:\n" + result.output[-3000:]
    return "\n\n".join(
        f"{t.name}: {t.outcome.value}: {t.message or ''}\n{(t.details or '')[-1500:]}" for t in bad
    )
