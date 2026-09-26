# Coverage

Phase 6 measures **line and branch coverage** every time a test suite runs (Phase 5), maps it
onto the modules and functions found by the code analyzer (Phase 3), ranks the weakest,
riskiest functions, and uses that ranking to **guide AI test generation** (Phase 4). The
effect of generated tests can then be measured by comparing two executions.

Code: `src/testmind/coverage_engine/` (parser, report, comparison) and the coverage parts of
`src/testmind/sandbox/docker_sandbox.py` and `src/testmind/test_engine/runner.py`.

## Who decides what

| Question | Answered by |
|---|---|
| Which lines and branches ran? | **coverage.py** (7.16.1, pinned in the sandbox image), via pytest-cov |
| Which modules are production code, where do functions start and end? | The **code analysis** (Phase 3, AST) |
| Which functions are the weakest spots? | A fixed formula (below) |
| Which tests to write for them? | The **LLM**, validated by the sandbox as in Phase 4 |

No LLM is involved in measuring or ranking. The model only receives the result.

## Measurement

Each test execution runs pytest in the sandbox with:

```text
--cov=/workspace/src --cov-config=/opt/testmind/coveragerc --cov-report=json:/out/coverage.json
```

[`docker/coveragerc`](../docker/coveragerc) enables branch coverage, writes its data file to
the container's `/tmp`, and omits the generated tests. Because TestMind passes its **own**
config file, a project's `[tool.coverage]` settings (`fail_under`, `omit`, plugins) cannot
change the result or fail the run. A sandbox test proves this with a project that sets
`fail_under = 100` and omits its own package.

The JSON report is read back from `/out`. It is produced next to untrusted code, so it is
handled as untrusted input:
- its size is capped (50 MB);
- its shape is validated;
- file names are only matched against files TestMind already knows, never opened.

## What counts as production code

Production code is every module of the run's code analysis, except test support code:
- `conftest.py`;
- anything inside a `tests/` or `test/` directory, such as `tests/__init__.py` and helpers.

Test modules themselves are already excluded by the code analysis.

A production module that coverage.py reported nothing for is listed under
`unmeasured_files`. It is not counted as 0 % or 100 % in the totals. With `source` set and
`include_namespace_packages` enabled this is rare, because modules that no test imports are
reported as 0 % by coverage.py.

## Report

| Level | Contents |
|---|---|
| Totals | statements, covered lines, branches, covered branches, line %, branch %, combined %, files, functions, functions never executed |
| File | the counts above, `missing` line ranges (`"15-18"`), exact `missing_lines`, and branches never taken (`"2->3"`, `"8->exit"`) |
| Function | the same per function, plus complexity, findings (rule ids) and `is_public` |
| Gaps | functions below the threshold, riskiest first, with reasons |

The combined percentage is coverage.py's own:
`(covered lines + covered branches) / (statements + branches)`. Anything with nothing to
cover counts as 100 %, as in coverage.py.

### Function coverage

A function's statements are the lines after its `def` up to its last line, minus the bodies
of functions nested in it. The `def` line itself runs when the enclosing scope runs, not when
the function is called, so it belongs to the enclosing scope. Branches belong to the function
whose statement they start from.

TestMind computes this itself instead of reading coverage.py's function regions. Regions
exist only in coverage.py ≥ 7.5, and a project may install another version into its runner
image ([ADR-0007](adr/0007-own-function-coverage-mapping.md)). The sandbox tests compare the
result with coverage.py's own numbers on every example repository and on a module with
decorators, nested functions, local classes, properties, async functions and multi-line
signatures. They agree exactly, with **one documented difference**. The decorator line of a
**nested** decorated function (for example `@functools.wraps(f)` inside a decorator) runs
when the enclosing function is called, so TestMind counts it in that function. coverage.py's
regions leave it to the module. File totals are identical in every case.

A single-line function (`def f(): return 1`) has no statement of its own. Line coverage
cannot tell whether it was called, so it is reported with 0 statements (100 %) and never as a
gap.

### Gap ranking

```text
risk = uncovered share × (cyclomatic complexity + 2 × static-analysis findings)
```

`uncovered share = 1 − percent / 100`. Only functions with at least one statement and a
coverage below the threshold (`TESTMIND_COVERAGE_THRESHOLD`, default 80) are gaps. Ties are
broken by lower coverage, then path and line. Every gap explains itself, for example:

```json
{
  "qualname": "price_for",
  "percent": 0.0,
  "risk": 17.0,
  "reasons": [
    "never executed by the test suite",
    "22 of 22 branches never taken",
    "cyclomatic complexity 15",
    "static-analysis findings: TM030"
  ]
}
```

## Coverage-guided test generation

`POST /api/v1/runs/{run_id}/test-generations` accepts `coverage_execution_id`, a succeeded
execution of the same run that has a coverage report.

- **Target selection** keeps only functions that are not fully covered. Functions in
  unmeasured modules count as uncovered. They are ranked by the same risk formula.
- **The prompt** gets an extra, separately versioned section
  ([`coverage.v1.md`](../src/testmind/ai_engine/prompts/coverage.v1.md)). It lists, for each
  target, its coverage, the lines never executed and the branches never taken, and asks for
  tests that make exactly those run. The generation records `prompt_version` as
  `v1+coverage.v1`. Without coverage the prompt is unchanged (`v1`).
- Validation is unchanged: static checks and a real sandbox run decide.

## Measuring the effect

Run the suite again with the generated tests and compare it with the baseline:

```text
POST /runs/{run}/test-executions                             → baseline (coverage)
POST /runs/{run}/test-generations {"coverage_execution_id"}  → tests for the gaps
POST /runs/{run}/test-executions  {"generation_id"}          → suite + generated tests
GET  /test-executions/{new}/coverage/compare?base={baseline} → delta
```

Verified with the real sandbox and database on `examples/buggy_inventory`, with the LLM
replaced by a scripted reply (no API key available). The riskiest gap selected was
`price_for` (complexity 15, a finding, never executed). The generated tests raised total
coverage from **8.47 % to 38.98 %** and `price_for` from **0 % to 77.27 %**. Lines 14, 18, 19
and 23 remain uncovered, and no previously covered line was lost
(`tests/sandbox/test_coverage_guided_generation.py`).

## When there is no coverage

The execution summary has `coverage_percent: null` and a `coverage_error` explaining why, and
the coverage endpoints answer `404` with the same reason:

| Situation | Reason |
|---|---|
| No tests, sandbox unavailable, install failed | `Coverage was not measured: <outcome>` |
| Timeout or crash before pytest finished | `coverage.py wrote no report (the run timed out or pytest crashed)` |
| Report over 50 MB | `The coverage report exceeds 50 MB` |
| Not coverage.py JSON | `Not a coverage.py JSON report: N errors` |

A run whose tests **fail** still has coverage. It reflects the tests that ran.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TESTMIND_COVERAGE_THRESHOLD` | `80` | Functions below this percentage are gaps |
