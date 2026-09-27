"""Mutation testing: do the tests notice when the code is wrong?

1. Run the suite once with per-test coverage (the baseline). Tests that already fail are
   excluded: they would "kill" every mutant.
2. Generate mutants in production functions, riskiest functions first (complexity and
   static-analysis findings), up to a limit.
3. For each mutant, the tests that executed its lines at baseline are the only ones run
   against it. A mutant that no test executes is `no_coverage` and is not run.
4. Run everything in one sandbox container (docker/testmind_mutate.py), each mutant with a
   timeout derived from its tests' baseline duration.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from testmind.analyzers.code.models import CodeProfile, FunctionInfo
from testmind.analyzers.repository.limits import AnalysisLimits
from testmind.analyzers.repository.models import RepositoryProfile
from testmind.analyzers.repository.walker import read_relative
from testmind.coverage_engine.impact import TestImpact
from testmind.coverage_engine.report import FINDING_WEIGHT, is_production
from testmind.mutation_engine.models import (
    FileScore,
    FunctionScore,
    MutantResult,
    MutantStatus,
    MutationReport,
    Score,
)
from testmind.mutation_engine.operators import Mutant, generate_mutants
from testmind.regression.selection import short_nodeid
from testmind.sandbox.docker_sandbox import MutationJob, MutationResult
from testmind.sandbox.models import Outcome, SandboxUnavailableError
from testmind.test_engine.models import ExecutionOutcome, TestRunReport
from testmind.test_engine.runner import TestSuiteRunner

MIN_TIMEOUT = 5.0
TIMEOUT_FACTOR = 5
JOB_OVERHEAD = 60
NOT_RUNNABLE = frozenset(
    {
        ExecutionOutcome.NO_TESTS,
        ExecutionOutcome.SANDBOX_UNAVAILABLE,
        ExecutionOutcome.INSTALL_FAILED,
        ExecutionOutcome.TIMED_OUT,
    }
)


class MutationSandbox(Protocol):
    def run_mutants(self, repo_root: Path, job: MutationJob) -> MutationResult: ...


class _DriverResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(max_length=64)
    status: MutantStatus
    duration: float | None = None
    killed_by: str | None = Field(default=None, max_length=2000)
    detail: str | None = Field(default=None, max_length=8000)


class _DriverOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_DriverResult] = Field(max_length=10_000)


class MutationEngine:
    def __init__(
        self,
        runner: TestSuiteRunner,
        sandbox: MutationSandbox,
        *,
        max_mutants: int,
        mutant_timeout_cap: float,
        job_timeout_cap: int,
        limits: AnalysisLimits | None = None,
    ) -> None:
        self._runner = runner
        self._sandbox = sandbox
        self._max_mutants = max_mutants
        self._mutant_timeout_cap = mutant_timeout_cap
        self._job_timeout_cap = job_timeout_cap
        self._limits = limits or AnalysisLimits()

    def run(self, root: Path, repository: RepositoryProfile, code: CodeProfile) -> MutationReport:
        baseline = self._runner.run(root, repository, code=code, test_contexts=True)
        report = baseline.report
        failing = {t.nodeid for t in report.tests if t.outcome in {Outcome.FAILED, Outcome.ERROR}}
        notes: list[str] = []
        if report.outcome in NOT_RUNNABLE:
            notes.append(f"The baseline run did not run tests ({report.outcome.value})")
        elif baseline.impact is None:
            notes.append(
                "No per-test coverage from the baseline run: mutants cannot be targeted "
                f"({report.coverage_error or 'coverage was not measured'})"
            )
        if notes or baseline.impact is None:
            return _report(report, failing, 0, [], notes)

        mutants, generated = self._mutants(root, code)
        if generated > len(mutants):
            notes.append(f"{generated} mutants generated; the {len(mutants)} riskiest were run")
        if failing:
            notes.append(f"{len(failing)} test(s) already fail without mutation and were excluded")

        plans = [
            _plan(m, baseline.impact, failing, report, self._mutant_timeout_cap) for m in mutants
        ]
        runnable = [p for p in plans if p.tests]
        outcomes = self._execute(root, report, runnable, repository, notes) if runnable else {}
        results = [p.result(outcomes.get(p.mutant.id)) for p in plans]
        return _report(report, failing, generated, results, notes)

    def _mutants(self, root: Path, code: CodeProfile) -> tuple[list[Mutant], int]:
        by_path: dict[str, list[FunctionInfo]] = defaultdict(list)
        for function in code.functions:
            if is_production(function.path):
                by_path[function.path].append(function)
        findings = Counter((f.path, f.symbol) for f in code.findings)
        risk = {
            (f.path, f.qualname): f.complexity + FINDING_WEIGHT * findings[(f.path, f.qualname)]
            for functions in by_path.values()
            for f in functions
        }
        mutants: list[Mutant] = []
        for path, functions in sorted(by_path.items()):
            source = read_relative(root, path, self._limits)
            if source is not None:
                mutants += generate_mutants(path, source, functions)
        mutants.sort(key=lambda m: (-risk.get((m.path, m.function), 0), m.path, m.start))
        return mutants[: self._max_mutants], len(mutants)

    def _execute(
        self,
        root: Path,
        report: TestRunReport,
        plans: list[_Plan],
        repository: RepositoryProfile,
        notes: list[str],
    ) -> dict[str, _DriverResult]:
        timeout = int(min(sum(p.timeout for p in plans) + JOB_OVERHEAD, self._job_timeout_cap))
        env = (
            {"DJANGO_SETTINGS_MODULE": repository.django_settings_module}
            if repository.django_settings_module
            else {}
        )
        job = MutationJob(
            image=report.install.image if report.install else "",
            mutants=[p.payload() for p in plans],
            timeout_seconds=timeout,
            env=env,
        )
        try:
            run = self._sandbox.run_mutants(root, job)
        except SandboxUnavailableError as exc:
            notes.append(f"Mutants were not run: {exc}")
            return {}
        if run.timed_out:
            notes.append(
                f"The mutation run exceeded {timeout} s; mutants without a result are errors"
            )
        if run.results_json is None:
            notes.append("The mutation driver wrote no results: " + run.output[-500:])
            return {}
        try:
            output = _DriverOutput.model_validate_json(run.results_json)
        except ValidationError as exc:
            notes.append(f"The mutation driver's results are invalid ({exc.error_count()} errors)")
            return {}
        return {r.id: r for r in output.results}


class _Plan:
    def __init__(self, mutant: Mutant, tests: list[str], timeout: float) -> None:
        self.mutant = mutant
        self.tests = tests
        self.timeout = timeout

    def payload(self) -> dict[str, object]:
        m = self.mutant
        return {
            "id": m.id, "path": m.path, "start": m.start, "end": m.end, "original": m.original,
            "replacement": m.replacement, "tests": self.tests, "timeout": self.timeout,
        }  # fmt: skip

    def result(self, outcome: _DriverResult | None) -> MutantResult:
        m = self.mutant
        if not self.tests:
            status, detail = MutantStatus.NO_COVERAGE, None
        elif outcome is None:
            status, detail = MutantStatus.ERROR, "no result from the mutation driver"
        else:
            status, detail = outcome.status, outcome.detail
        return MutantResult(
            id=m.id,
            path=m.path,
            function=m.function,
            line=m.line,
            column=m.column,
            start=m.start,
            end=m.end,
            operator=m.operator.value,
            original=m.original,
            replacement=m.replacement,
            status=status,
            tests=len(self.tests),
            killed_by=outcome.killed_by if outcome else None,
            duration_seconds=outcome.duration if outcome else None,
            detail=detail,
        )


def _plan(
    mutant: Mutant, impact: TestImpact, failing: set[str], report: TestRunReport, cap: float
) -> _Plan:
    tests = sorted(
        {
            test
            for line in range(mutant.line, mutant.end_line + 1)
            for test in impact.tests_for_line(mutant.path, line)
            if short_nodeid(test) not in failing
        }
    )
    durations = {t.nodeid: t.duration_seconds for t in report.tests}
    baseline = sum(durations.get(short_nodeid(t), 0.0) for t in tests)
    timeout = min(max(MIN_TIMEOUT, MIN_TIMEOUT + TIMEOUT_FACTOR * baseline), cap)
    return _Plan(mutant, tests, round(timeout, 1))


# ---------------------------------------------------------------------------------- scores
def _score(results: Iterable[MutantResult]) -> Score:
    counts = Counter(r.status for r in results)
    detected = counts[MutantStatus.KILLED] + counts[MutantStatus.TIMEOUT]
    survived = counts[MutantStatus.SURVIVED]
    no_coverage = counts[MutantStatus.NO_COVERAGE]
    judged = detected + survived + no_coverage
    return Score(
        total=sum(counts.values()),
        killed=counts[MutantStatus.KILLED],
        survived=survived,
        timeout=counts[MutantStatus.TIMEOUT],
        no_coverage=no_coverage,
        errors=counts[MutantStatus.ERROR],
        score=round(100 * detected / judged, 2) if judged else None,
        covered_score=round(100 * detected / (detected + survived), 2)
        if detected + survived
        else None,
    )


def _report(
    report: TestRunReport,
    failing: set[str],
    generated: int,
    results: list[MutantResult],
    notes: list[str],
) -> MutationReport:
    by_file: dict[str, list[MutantResult]] = defaultdict(list)
    by_function: dict[tuple[str, str], list[MutantResult]] = defaultdict(list)
    for result in results:
        by_file[result.path].append(result)
        by_function[(result.path, result.function)].append(result)
    return MutationReport(
        outcome=report.outcome,
        baseline_totals=report.totals,
        excluded_tests=sorted(failing),
        generated=generated,
        totals=_score(results),
        files=[
            FileScore(path=path, **_score(items).model_dump())
            for path, items in sorted(by_file.items())
        ],
        functions=[
            FunctionScore(path=path, function=function, **_score(items).model_dump())
            for (path, function), items in sorted(by_function.items())
        ],
        mutants=results,
        duration_seconds=report.duration_seconds + sum(r.duration_seconds or 0 for r in results),
        notes=notes,
    )
