"""AI-written API tests for endpoint gaps, accepted only when the sandbox proves them.

For each gap of an execution's API coverage (untested endpoints first, then endpoints tested
only with errors or only with success), up to a limit:
1. The model gets the endpoint, what is missing, how a test obtains the application, the
   handler's source and an existing test module of the project, and writes a test module.
2. Static checks, as for generated tests (parses, has tests, imports the application, no
   network, process or file-deleting calls).
3. The module runs in the sandbox with the API capture: every test must pass, and a passing
   test must send the request that closes the gap (any request for an untested endpoint, a
   2xx/3xx or a 4xx answer for the other two), as recorded by the `testmind_api` plugin.
4. On failure the concrete problem goes back to the model once (bounded).

The model's own claims are never used; a suggestion is `accepted` only after 3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from testmind.ai_engine.llm import LLMError, LLMProvider, LLMRequest
from testmind.ai_engine.prompts import load_prompt
from testmind.ai_engine.test_generator.code_checks import check_code, count_tests, extract_code
from testmind.analyzers.code.analyzer import source_lines
from testmind.analyzers.code.models import CodeProfile
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.walker import read_relative
from testmind.api_engine.capture import ApiCaptureError, RawApiCapture, parse_api_json
from testmind.api_engine.coverage import CLIENT_ERROR, SERVER_ERROR, SUCCESS, attribute_call
from testmind.api_engine.models import (
    ApiApp,
    ApiCoverageReport,
    ApiGap,
    ApiSurface,
    Endpoint,
    EndpointCoverage,
    Framework,
)
from testmind.mutation_engine.suggester import SuggestionAttempt, SuggestionStatus
from testmind.sandbox.docker_sandbox import PytestJob
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError
from testmind.sandbox.paths import report_path

PROMPT_VERSION = 1
MAX_ATTEMPTS = 2
MAX_SOURCE_CHARS = 12_000
MAX_EXAMPLE_CHARS = 8_000
MAX_FEEDBACK = 3000
TARGET_KINDS = ("untested", "error_only", "success_only")  # a 500 is a bug to fix, not a test
GOALS = {
    "untested": "send requests to it and check the answers (a successful request, and a "
    "failing one where the endpoint can reject input)",
    "error_only": "check a SUCCESSFUL request (2xx or 3xx) and its response",
    "success_only": "check that invalid input or a missing resource is REJECTED with the "
    "right client error (4xx)",
}


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ApiSuggestion(_Model):
    method: str
    path: str
    gap: str
    status: SuggestionStatus
    test_path: str
    code: str | None
    test_count: int
    statuses: list[int] = Field(description="Answers the accepted tests received from it")
    attempts: list[SuggestionAttempt]


class ApiSuggestionResult(_Model):
    suggestions: list[ApiSuggestion]
    accepted: int
    model: str | None
    prompt_version: str
    input_tokens: int
    output_tokens: int
    notes: list[str]


class ApiSuggestionSandbox(Protocol):
    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult: ...


@dataclass(frozen=True, slots=True)
class _Context:
    report: ApiCoverageReport
    surface: ApiSurface
    code: CodeProfile | None
    example: str  # an existing test module of the project that calls the application


class _Usage:
    def __init__(self) -> None:
        self.model: str | None = None
        self.input_tokens = 0
        self.output_tokens = 0


class ApiTestSuggester:
    def __init__(
        self,
        provider: LLMProvider,
        sandbox: ApiSuggestionSandbox,
        *,
        image: str | None,
        max_tokens: int,
        max_endpoints: int,
        env: dict[str, str] | None = None,
        limits: AnalysisLimits | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._sandbox = sandbox
        self._image = image
        self._max_tokens = max_tokens
        self._max_endpoints = max_endpoints
        self._env = env or {}
        self._limits = limits or AnalysisLimits()
        self._system = load_prompt("test_generation_system", PROMPT_VERSION)
        self._write = load_prompt("write_api_test", PROMPT_VERSION)
        self._fix = load_prompt("fix_api_test", PROMPT_VERSION)
        self._on_progress = on_progress or (lambda _: None)

    def suggest(
        self, root: Path, report: ApiCoverageReport, surface: ApiSurface, code: CodeProfile | None
    ) -> ApiSuggestionResult:
        gaps = [g for g in report.gaps if g.kind in TARGET_KINDS]
        notes = []
        if len(gaps) > self._max_endpoints:
            notes.append(f"{len(gaps)} endpoint gaps; the first {self._max_endpoints} were used")
        context = _Context(report, surface, code, self._example(root, report))
        usage = _Usage()
        suggestions = []
        for number, gap in enumerate(gaps[: self._max_endpoints], start=1):
            self._on_progress(f"{gap.method} {gap.path}")
            suggestions.append(
                self._suggest(root, gap, number=number, context=context, usage=usage)
            )
        return ApiSuggestionResult(
            suggestions=suggestions,
            accepted=sum(1 for s in suggestions if s.status is SuggestionStatus.ACCEPTED),
            model=usage.model,
            prompt_version=f"v{PROMPT_VERSION}",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            notes=notes,
        )

    # ------------------------------------------------------------------------ one endpoint
    def _suggest(
        self,
        root: Path,
        gap: ApiGap,
        *,
        number: int,
        context: _Context,
        usage: _Usage,
    ) -> ApiSuggestion:
        endpoint = _endpoint(context.surface, gap)
        report = context.report
        framework = endpoint.framework if endpoint is not None else _framework_of(context.surface)
        app = _app(context.surface, endpoint)
        test_path = f"testmind/test_api_{number:02d}.py"
        modules = _accepted_modules(framework, app, gap)
        prompt = self._write.render(
            framework=framework.value if framework is not None else "web",
            method=gap.method,
            path=gap.path,
            params=", ".join(f"{p.name} ({p.type})" for p in endpoint.params)
            if endpoint is not None and endpoint.params
            else "none",
            body="yes" if endpoint is not None and endpoint.accepts_body else "no or unknown",
            gap=gap.reason,
            goal=GOALS[gap.kind],
            client=_client(framework, app),
            handler_path=gap.handler.path if gap.handler and gap.handler.path else "unknown",
            handler=self._handler_source(root, gap, context.code),
            example=context.example,
        )
        attempts: list[SuggestionAttempt] = []
        code_text: str | None = None
        statuses: list[int] = []
        status = SuggestionStatus.REJECTED
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                reply = self._ask(prompt, usage)
            except LLMError as exc:
                attempts.append(
                    SuggestionAttempt(attempt=attempt, outcome="llm_error", feedback=str(exc))
                )
                status = SuggestionStatus.LLM_ERROR
                break
            candidate = extract_code(reply)
            problems = ["No Python code block"] if candidate is None else _check(candidate, modules)
            if candidate is None or problems:
                feedback = "\n".join(f"- {p}" for p in problems)
                attempts.append(
                    SuggestionAttempt(attempt=attempt, outcome="rejected", feedback=feedback)
                )
            else:
                code_text = candidate
                try:
                    outcome, feedback, statuses = self._judge(
                        root, test_path, candidate, gap, report.endpoints
                    )
                except SandboxUnavailableError as exc:
                    attempts.append(
                        SuggestionAttempt(
                            attempt=attempt, outcome="sandbox_error", feedback=str(exc)
                        )
                    )
                    status = SuggestionStatus.SANDBOX_ERROR
                    break
                attempts.append(
                    SuggestionAttempt(attempt=attempt, outcome=outcome, feedback=feedback or None)
                )
                if outcome == "accepted":
                    status = SuggestionStatus.ACCEPTED
                    break
            prompt = self._fix.render(
                method=gap.method,
                path=gap.path,
                feedback=feedback[:MAX_FEEDBACK],
                code=candidate or reply[:MAX_FEEDBACK],
                goal=GOALS[gap.kind],
            )
        return ApiSuggestion(
            method=gap.method,
            path=gap.path,
            gap=gap.kind,
            status=status,
            test_path="tests/" + test_path,
            code=code_text,
            test_count=count_tests(code_text) if code_text else 0,
            statuses=statuses if status is SuggestionStatus.ACCEPTED else [],
            attempts=attempts,
        )

    def _judge(
        self,
        root: Path,
        test_path: str,
        code: str,
        gap: ApiGap,
        report_endpoints: list[EndpointCoverage],
    ) -> tuple[str, str, list[int]]:
        """(outcome, feedback for the model, answers the passing tests got from the endpoint)."""
        job = PytestJob(
            test_files={test_path: code}, image=self._image, env=self._env, capture_api=True
        )
        result = self._sandbox.run(root, job)
        bad = [t for t in result.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}]
        if result.timed_out or bad or not result.tests or result.exit_code != 0:
            details = "\n".join(f"{t.name}: {t.message or ''}" for t in bad)
            return (
                "fails_on_original",
                (
                    "The tests do not pass against the application:\n"
                    + (details or result.output[-MAX_FEEDBACK:])
                ),
                [],
            )
        capture = _capture(result)
        passed = {t.name for t in result.tests if t.outcome is Outcome.PASSED}
        answers = _answers(capture, report_endpoints, gap, test_path, passed)
        if not answers:
            return (
                "endpoint_not_called",
                (
                    f"No passing test sent a request to {gap.method} {gap.path} (as recorded by "
                    "the application). Call exactly this endpoint."
                ),
                [],
            )
        if not _closes(gap.kind, answers):
            wanted = "a successful answer (2xx or 3xx)" if gap.kind == "error_only" else "a 4xx"
            return (
                "gap_not_closed",
                (
                    f"The tests only received {sorted(set(answers))} from {gap.method} {gap.path}; "
                    f"the gap needs {wanted}."
                ),
                [],
            )
        return "accepted", "", sorted(answers)

    # ------------------------------------------------------------------------------ context
    def _handler_source(self, root: Path, gap: ApiGap, code: CodeProfile | None) -> str:
        handler = gap.handler
        if handler is None or handler.path is None or handler.line is None:
            return "# the handler was not found statically"
        source = read_relative(root, handler.path, self._limits)
        if source is None:
            return "# the handler's file could not be read"
        end = next(
            (
                f.end_line
                for f in (code.functions if code is not None else [])
                if f.path == handler.path and f.qualname == handler.qualname
            ),
            None,
        ) or next(
            (
                c.end_line
                for c in (code.classes if code is not None else [])
                if c.path == handler.path and c.qualname == handler.qualname
            ),
            handler.line,
        )
        lines = source_lines(source)
        return "\n".join(lines[handler.line - 1 : end])[:MAX_SOURCE_CHARS]

    def _example(self, root: Path, report: ApiCoverageReport) -> str:
        files = sorted({t.split("::")[0] for e in report.endpoints for t in e.tests})
        for file in files:
            source = read_relative(root, file, self._limits)
            if source is not None:
                return source[:MAX_EXAMPLE_CHARS]
        return "# no test of the project calls the application"

    def _ask(self, prompt: str, usage: _Usage) -> str:
        response = self._provider.complete(
            LLMRequest(system=self._system.render(), prompt=prompt, max_tokens=self._max_tokens)
        )
        usage.model = response.model
        usage.input_tokens += response.input_tokens
        usage.output_tokens += response.output_tokens
        return response.text


# ------------------------------------------------------------------------------ helpers
def _endpoint(surface: ApiSurface, gap: ApiGap) -> Endpoint | None:
    return next(
        (e for e in surface.endpoints if e.path == gap.path and e.method == gap.method), None
    )


def _framework_of(surface: ApiSurface) -> Framework | None:
    return surface.apps[0].framework if surface.apps else None


def _app(surface: ApiSurface, endpoint: Endpoint | None) -> ApiApp | None:
    if endpoint is None:
        return None
    return next(
        (a for a in surface.apps if a.target is not None and a.target == endpoint.app),
        next((a for a in surface.apps if a.framework is endpoint.framework), None),
    )


def _client(framework: Framework | None, app: ApiApp | None) -> str:
    if framework is Framework.DJANGO:
        return (
            "Use `django.test.TestCase` and its `self.client`; the test runner creates the "
            "database and DJANGO_SETTINGS_MODULE is set."
        )
    if app is None or app.target is None:
        return "Obtain the application the way the existing test module does."
    module, _, attribute = app.target.partition(":")
    name = attribute.removesuffix("()")
    make = f"{name}()" if attribute.endswith("()") else name
    if framework is Framework.FLASK:
        return f"from {module} import {name}\nclient = {make}.test_client()"
    return (
        f"from fastapi.testclient import TestClient\nfrom {module} import {name}\n"
        f"client = TestClient({make})"
    )


def _accepted_modules(
    framework: Framework | None, app: ApiApp | None, gap: ApiGap
) -> tuple[str, ...]:
    """Imports that tie the test to the application under test (at least one is required)."""
    modules = [app.module] if app is not None else []
    if gap.handler is not None:
        modules.append(gap.handler.module)
    if framework is Framework.DJANGO:
        modules.append("django.test")
    return tuple(dict.fromkeys(modules))


def _check(code: str, modules: tuple[str, ...]) -> list[str]:
    """The static checks, with any one of `modules` imported as the module under test."""
    if not modules:
        return check_code(code, None)
    return min((check_code(code, module) for module in modules), key=len)


def _capture(result: SandboxResult) -> RawApiCapture | None:
    if result.api_json is None:
        return None
    try:
        return parse_api_json(result.api_json)
    except ApiCaptureError:
        return None


def _answers(
    capture: RawApiCapture | None,
    endpoints: list[EndpointCoverage],
    gap: ApiGap,
    test_path: str,
    passed: set[str],
) -> list[int]:
    """Statuses the candidate's passing tests received from the gap's endpoint."""
    if capture is None:
        return []
    answers: list[int] = []
    for call in capture.calls:
        located = report_path(call.test_path, capture.rootdir) if call.test_path else None
        if located is None or located[0] != "tests/" + test_path or call.test_name not in passed:
            continue
        endpoint = attribute_call(call, endpoints)
        if (
            endpoint is not None
            and (endpoint.method, endpoint.path) == (gap.method, gap.path)
            and call.status is not None
        ):
            answers += [call.status] * call.count
    return answers


def _closes(kind: str, answers: list[int]) -> bool:
    if kind == "error_only":
        return any(SUCCESS <= a < CLIENT_ERROR for a in answers)
    if kind == "success_only":
        return any(CLIENT_ERROR <= a < SERVER_ERROR for a in answers)
    return True  # untested: any request closes it
