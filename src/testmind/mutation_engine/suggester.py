"""AI-written tests for surviving mutants, accepted only when the sandbox proves them useful.

For each surviving mutant (riskiest first, up to a limit):
1. The model gets the mutant (original and changed code, the line in context, the tests that
   already run it) and writes a test module.
2. Static checks, as for generated tests: the code parses, has tests, imports the module under
   test, and uses no network, process or file-deleting calls.
3. The test must PASS on the original code (normal sandbox run).
4. The test must KILL the mutant (mutation driver, with the test added to the working copy).
5. On failure the concrete problem goes back to the model once (bounded).

A suggestion is `accepted` only after 3 and 4. The model's own claims are never used.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from testmind.ai_engine.llm import LLMError, LLMProvider, LLMRequest
from testmind.ai_engine.prompts import load_prompt
from testmind.ai_engine.test_generator.code_checks import check_code, count_tests, extract_code
from testmind.analyzers.repository.entry_points import module_name
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.walker import read_relative
from testmind.mutation_engine.models import MutantResult, MutantStatus, MutationReport
from testmind.sandbox.docker_sandbox import MutationJob, MutationResult
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError

PROMPT_VERSION = 1
MAX_ATTEMPTS = 2
CONTEXT_LINES = 4
MAX_SOURCE_CHARS = 40_000
MAX_FEEDBACK = 3000
KILL_TIMEOUT = 60.0


class SuggestionStatus(StrEnum):
    ACCEPTED = "accepted"  # passes on the original code and kills the mutant
    REJECTED = "rejected"  # never produced a test that did both
    LLM_ERROR = "llm_error"
    SANDBOX_ERROR = "sandbox_error"  # the sandbox could not judge it


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SuggestionAttempt(_Model):
    attempt: int
    outcome: str  # rejected, fails_on_original, mutant_survives, accepted, llm_error, …
    feedback: str | None = None


class MutantSuggestion(_Model):
    mutant_id: str
    path: str
    function: str
    line: int
    original: str
    replacement: str
    status: SuggestionStatus
    test_path: str
    code: str | None
    test_count: int
    attempts: list[SuggestionAttempt]


class SuggestionResult(_Model):
    suggestions: list[MutantSuggestion]
    accepted: int
    model: str | None
    prompt_version: str
    input_tokens: int
    output_tokens: int


class SuggestionSandbox(Protocol):
    def run_pytest(
        self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
    ) -> SandboxResult: ...

    def run_mutants(self, repo_root: Path, job: MutationJob) -> MutationResult: ...


@dataclass(slots=True)
class _Usage:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class _Attempts:
    items: list[SuggestionAttempt] = field(default_factory=list)

    def add(self, outcome: str, feedback: str | None = None) -> None:
        self.items.append(
            SuggestionAttempt(attempt=len(self.items) + 1, outcome=outcome, feedback=feedback)
        )


class MutantTestSuggester:
    def __init__(
        self,
        provider: LLMProvider,
        sandbox: SuggestionSandbox,
        *,
        image: str | None,
        max_tokens: int,
        max_mutants: int,
        env: dict[str, str] | None = None,
        limits: AnalysisLimits | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._image = image
        self._max_tokens = max_tokens
        self._max_mutants = max_mutants
        self._env = env or {}
        self._limits = limits or AnalysisLimits()
        self._system = load_prompt("test_generation_system", PROMPT_VERSION)
        self._kill = load_prompt("kill_mutant", PROMPT_VERSION)
        self._fix = load_prompt("fix_mutant_test", PROMPT_VERSION)
        self._on_progress = on_progress or (lambda _: None)

    def suggest(self, root: Path, report: MutationReport) -> SuggestionResult:
        survivors = [m for m in report.mutants if m.status is MutantStatus.SURVIVED]
        usage = _Usage()
        suggestions = []
        for mutant in survivors[: self._max_mutants]:
            self._on_progress(mutant.id)
            suggestions.append(self._suggest(root, mutant, usage))
        return SuggestionResult(
            suggestions=suggestions,
            accepted=sum(1 for s in suggestions if s.status is SuggestionStatus.ACCEPTED),
            model=usage.model,
            prompt_version=f"v{PROMPT_VERSION}",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    def _suggest(self, root: Path, mutant: MutantResult, usage: _Usage) -> MutantSuggestion:
        module = module_name(mutant.path)
        test_path = f"testmind/test_kill_{mutant.id}.py"
        source = read_relative(root, mutant.path, self._limits) or ""
        prompt = self._kill.render(
            module=module,
            path=mutant.path,
            function=mutant.function,
            line=str(mutant.line),
            original=mutant.original,
            replacement=mutant.replacement,
            context=_context(source, mutant.line),
            tests=f"{mutant.tests} test(s) of the existing suite",
            source=source[:MAX_SOURCE_CHARS],
        )
        attempts = _Attempts()
        code: str | None = None
        status = SuggestionStatus.REJECTED
        for _ in range(MAX_ATTEMPTS):
            try:
                reply = self._ask(prompt, usage)
            except LLMError as exc:
                attempts.add("llm_error", str(exc))
                status = SuggestionStatus.LLM_ERROR
                break
            candidate = extract_code(reply)
            problems = (
                ["No Python code block"] if candidate is None else check_code(candidate, module)
            )
            if candidate is None or problems:
                feedback = "\n".join(f"- {p}" for p in problems)
                attempts.add("rejected", feedback)
            else:
                code = candidate
                try:
                    outcome, feedback = self._judge(root, mutant, test_path, candidate)
                except SandboxUnavailableError as exc:
                    attempts.add("sandbox_error", str(exc))
                    status = SuggestionStatus.SANDBOX_ERROR
                    break
                attempts.add(outcome, feedback or None)
                if outcome == "accepted":
                    status = SuggestionStatus.ACCEPTED
                    break
            prompt = self._fix.render(
                module=module,
                feedback=feedback[:MAX_FEEDBACK],
                code=candidate or reply[:MAX_FEEDBACK],
                original=mutant.original,
                replacement=mutant.replacement,
                line=str(mutant.line),
            )
        return MutantSuggestion(
            mutant_id=mutant.id,
            path=mutant.path,
            function=mutant.function,
            line=mutant.line,
            original=mutant.original,
            replacement=mutant.replacement,
            status=status,
            test_path="tests/" + test_path,
            code=code,
            test_count=count_tests(code) if code else 0,
            attempts=attempts.items,
        )

    def _judge(
        self, root: Path, mutant: MutantResult, test_path: str, code: str
    ) -> tuple[str, str]:
        """(outcome, feedback for the model)."""
        original = self._sandbox.run_pytest(root, {test_path: code}, image=self._image)
        bad = [t for t in original.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}]
        if original.timed_out or bad or not original.tests or original.exit_code != 0:
            details = "\n".join(f"{t.name}: {t.message or ''}" for t in bad)
            return "fails_on_original", (
                "The test does not pass on the ORIGINAL code:\n"
                + (details or original.output[-MAX_FEEDBACK:])
            )
        work_path = f"tests/{test_path}"  # where the driver writes it inside the working copy
        job = MutationJob(
            image=self._image or "",
            mutants=[
                {
                    "id": mutant.id, "path": mutant.path, "start": mutant.start,
                    "end": mutant.end, "original": mutant.original,
                    "replacement": mutant.replacement, "tests": [work_path],
                    "timeout": KILL_TIMEOUT,
                }
            ],
            timeout_seconds=int(KILL_TIMEOUT) + 60,
            env=self._env,
            files={work_path: code},
        )  # fmt: skip
        result = self._sandbox.run_mutants(root, job)
        status = _driver_status(result)
        if status in {"killed", "timeout"}:
            return "accepted", ""
        if status == "survived":
            return "mutant_survives", (
                f"The test passes on the original code but ALSO passes when `{mutant.original}` "
                f"is changed to `{mutant.replacement}`: it does not check what the change breaks."
            )
        return "sandbox_error", f"The mutant could not be run ({status})"

    def _ask(self, prompt: str, usage: _Usage) -> str:
        response = self._provider.complete(
            LLMRequest(system=self._system.render(), prompt=prompt, max_tokens=self._max_tokens)
        )
        usage.model = response.model
        usage.input_tokens += response.input_tokens
        usage.output_tokens += response.output_tokens
        return response.text


def _context(source: str, line: int) -> str:
    lines = source.splitlines()
    first = max(1, line - CONTEXT_LINES)
    last = min(len(lines), line + CONTEXT_LINES)
    return "\n".join(
        f"{'>' if n == line else ' '} {n:>4} | {lines[n - 1]}" for n in range(first, last + 1)
    )


def _driver_status(result: MutationResult) -> str:
    if result.results_json is None:
        return "no result" + (" (timed out)" if result.timed_out else "")
    try:
        document = MutationDriverOutput.model_validate_json(result.results_json)
    except ValueError:
        return "invalid result"
    return document.results[0].status if document.results else "no result"


class _DriverEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str


class MutationDriverOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_DriverEntry]
