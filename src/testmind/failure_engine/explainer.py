"""AI hypotheses for failure groups, checked deterministically (ADR-0006 applied to diagnosis).

The model gets only the facts of a group (failure_engine.analyzer) and numbered source
excerpts. It returns blame, root cause, evidence, fix and confidence as JSON. TestMind then
checks the answer:
- the reply must be valid JSON of the required shape (one repair attempt);
- every evidence reference must point at a line the model was actually shown, or it is
  marked unverified;
- confidence is scaled by the share of verified evidence, and capped when nothing verifiable
  was cited.
Groups whose blame the facts already decide are not sent to the model at all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testmind.ai_engine.llm import LLMError, LLMProvider, LLMRequest
from testmind.ai_engine.prompts import load_prompt
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.walker import read_relative
from testmind.failure_engine.models import (
    AnalysisStatus,
    Blame,
    EvidenceReference,
    FailureAnalysisResult,
    FailureGroup,
    FailureHypothesis,
    FailureReport,
    FrameLocation,
)

PROMPT_VERSION = 1
CONTEXT_LINES = 3
MAX_FUNCTION_LINES = 60
MAX_TESTS_LISTED = 20
MAX_MESSAGE = 3000
UNSUPPORTED_CONFIDENCE = 0.3  # the most a hypothesis without verifiable evidence can get
JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


class _Reference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    path: str = Field(max_length=1024)
    line: int
    explanation: str = Field(max_length=2000)


class _Reply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    blame: Literal["code", "test", "environment"]
    root_cause: str = Field(min_length=1, max_length=4000)
    evidence: list[_Reference] = Field(max_length=20)
    suggested_fix: str = Field(max_length=4000)
    confidence: float = Field(ge=0, le=1)


@dataclass(slots=True)
class _Usage:
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class _Excerpts:
    """Numbered source excerpts shown to the model, and which lines they contain."""

    text: list[str] = field(default_factory=list)
    shown: dict[str, set[int]] = field(default_factory=dict)


class FailureExplainer:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_tokens: int,
        max_groups: int,
        limits: AnalysisLimits | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self._max_groups = max_groups
        self._limits = limits or AnalysisLimits()
        self._system = load_prompt("failure_analysis_system", PROMPT_VERSION)
        self._analyze = load_prompt("analyze_failure", PROMPT_VERSION)
        self._repair = load_prompt("repair_failure_analysis", PROMPT_VERSION)
        self._on_progress = on_progress or (lambda _: None)

    def explain(
        self, root: Path, report: FailureReport, generated: dict[str, str] | None = None
    ) -> FailureAnalysisResult:
        """`generated`: sources of generated test modules (they are not in the repository)."""
        usage = _Usage()
        hypotheses: list[FailureHypothesis] = []
        asked = 0
        for group in report.groups:
            if group.blame is not None:
                hypotheses.append(_deterministic(group))
            elif asked >= self._max_groups:
                hypotheses.append(_status(group, AnalysisStatus.SKIPPED, "Over the group limit"))
            else:
                asked += 1
                self._on_progress(group.id)
                hypotheses.append(self._explain(root, group, generated or {}, usage))
        return FailureAnalysisResult(
            hypotheses=hypotheses,
            model=usage.model,
            prompt_version=f"v{PROMPT_VERSION}",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )

    # ------------------------------------------------------------------------------ one group
    def _explain(
        self, root: Path, group: FailureGroup, generated: dict[str, str], usage: _Usage
    ) -> FailureHypothesis:
        excerpts = self._excerpts(root, group, generated)
        prompt = self._analyze.render(
            category=group.category.value,
            exception=group.exception_type or "(none)",
            message=group.message[:MAX_MESSAGE] or "(empty)",
            test_count=str(len(group.tests)),
            tests=_tests(group),
            evidence="\n".join(f"- {e.detail}" for e in group.evidence) or "(none)",
            frames=_frames(group),
            locals="\n".join(f"- {k} = {v}" for k, v in group.locals.items()) or "(none)",
            function=_function(group),
            sources="\n\n".join(excerpts.text) or "(no source available)",
        )
        problems: list[str] = []
        for attempt in (1, 2):
            request = (
                prompt
                if attempt == 1
                else prompt
                + "\n\n"
                + self._repair.render(problems="\n".join(f"- {p}" for p in problems))
            )
            try:
                reply = self._ask(request, usage)
            except LLMError as exc:
                return _status(group, AnalysisStatus.LLM_ERROR, str(exc))
            parsed, problems = _parse(reply)
            if parsed is not None:
                return _verified(group, parsed, excerpts)
        return _status(group, AnalysisStatus.LLM_ERROR, "No valid answer: " + "; ".join(problems))

    def _ask(self, prompt: str, usage: _Usage) -> str:
        response = self._provider.complete(
            LLMRequest(system=self._system.render(), prompt=prompt, max_tokens=self._max_tokens)
        )
        usage.model = response.model
        usage.input_tokens += response.input_tokens
        usage.output_tokens += response.output_tokens
        return response.text

    def _excerpts(self, root: Path, group: FailureGroup, generated: dict[str, str]) -> _Excerpts:
        wanted: dict[str, set[int]] = {}
        for frame in group.frames:
            if frame.location is FrameLocation.EXTERNAL:
                continue
            start = max(1, frame.line - CONTEXT_LINES)
            wanted.setdefault(frame.path, set()).update(
                range(start, frame.line + CONTEXT_LINES + 1)
            )
        if group.function is not None:
            end = min(group.function.end_line, group.function.line + MAX_FUNCTION_LINES - 1)
            wanted.setdefault(group.function.path, set()).update(
                range(group.function.line, end + 1)
            )

        excerpts = _Excerpts()
        for path in sorted(wanted):
            source = generated.get(path)
            if source is None:
                source = read_relative(root, path, self._limits)
            if source is None:
                continue
            lines = source.splitlines()
            shown = sorted(n for n in wanted[path] if 1 <= n <= len(lines))
            if not shown:
                continue
            excerpts.shown[path] = set(shown)
            body = []
            previous = None
            for number in shown:
                if previous is not None and number != previous + 1:
                    body.append("     …")
                body.append(f"{number:>5} | {lines[number - 1]}")
                previous = number
            excerpts.text.append(f"{path}:\n" + "\n".join(body))
        return excerpts


# ---------------------------------------------------------------------------------- helpers
def _parse(reply: str) -> tuple[_Reply | None, list[str]]:
    blocks = JSON_BLOCK.findall(reply)
    text = blocks[-1] if blocks else reply
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"The reply is not valid JSON ({exc.msg} at line {exc.lineno})"]
    try:
        return _Reply.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in error['loc']) or 'reply'}: {error['msg']}"
            for error in exc.errors()
        ]


def _verified(group: FailureGroup, reply: _Reply, excerpts: _Excerpts) -> FailureHypothesis:
    evidence = [
        EvidenceReference(
            path=ref.path,
            line=ref.line,
            explanation=ref.explanation,
            verified=ref.line in excerpts.shown.get(ref.path, set()),
        )
        for ref in reply.evidence
    ]
    verified = sum(1 for e in evidence if e.verified)
    notes: list[str] = []
    if not evidence:
        confidence = min(reply.confidence, UNSUPPORTED_CONFIDENCE)
        notes.append(f"No evidence was cited: confidence capped at {UNSUPPORTED_CONFIDENCE}")
    else:
        confidence = reply.confidence * verified / len(evidence)
        if verified < len(evidence):
            notes.append(
                f"{len(evidence) - verified} of {len(evidence)} evidence references point at "
                "lines the model was not shown; confidence reduced accordingly"
            )
    return FailureHypothesis(
        group_id=group.id,
        status=AnalysisStatus.ANALYSED,
        blame=Blame(reply.blame),
        root_cause=reply.root_cause,
        evidence=evidence,
        suggested_fix=reply.suggested_fix,
        model_confidence=reply.confidence,
        confidence=round(confidence, 2),
        notes=notes,
    )


def _deterministic(group: FailureGroup) -> FailureHypothesis:
    return FailureHypothesis(
        group_id=group.id,
        status=AnalysisStatus.DETERMINISTIC,
        blame=group.blame,
        root_cause=group.blame_reason,
        evidence=[],
        suggested_fix=None,
        model_confidence=None,
        confidence=1.0,
        notes=["Decided by the facts; the model was not asked"],
    )


def _status(group: FailureGroup, status: AnalysisStatus, note: str) -> FailureHypothesis:
    return FailureHypothesis(
        group_id=group.id,
        status=status,
        blame=None,
        root_cause=None,
        evidence=[],
        suggested_fix=None,
        model_confidence=None,
        confidence=None,
        notes=[note],
    )


def _tests(group: FailureGroup) -> str:
    listed = [
        f"- {t.nodeid}" + (" (generated by TestMind)" if t.generated else "")
        for t in group.tests[:MAX_TESTS_LISTED]
    ]
    if len(group.tests) > MAX_TESTS_LISTED:
        listed.append(f"- … and {len(group.tests) - MAX_TESTS_LISTED} more")
    return "\n".join(listed)


def _frames(group: FailureGroup) -> str:
    return (
        "\n".join(
            f"- {f.path}:{f.line} in {f.function} [{f.location.value}]: {f.code}"
            for f in group.frames
        )
        or "(none)"
    )


def _function(group: FailureGroup) -> str:
    f = group.function
    if f is None:
        return "(not in production code)"
    coverage = f"{f.coverage_percent}%" if f.coverage_percent is not None else "not measured"
    findings = ", ".join(f.findings) or "none"
    return (
        f"{f.qualname} ({f.path}:{f.line}-{f.end_line}), complexity {f.complexity}, "
        f"findings: {findings}, coverage in this run: {coverage}"
    )
