# Failure analysis

Phase 7 explains **why tests fail**, in two steps:
1. A **deterministic analysis** runs with every test execution. It categorises each failure,
   groups failures that share a cause, links them to the code analysis and coverage, and
   collects the facts as evidence. No LLM is involved.
2. An **AI analysis**, on request, proposes a root cause for each group, with blame, evidence,
   a fix and a confidence. TestMind then checks every cited line and adjusts the confidence.

Code: `src/testmind/failure_engine/` (capture, analyzer, explainer) and the sandbox plugin
[`docker/testmind_failures.py`](../docker/testmind_failures.py).

## Capturing failures: facts from pytest, not from text

Parsing formatted tracebacks is fragile, because the format depends on `--tb`, the pytest
version and plugins. Instead, the sandbox image contains a small pytest plugin that records
each failure as structured data while pytest still holds the exception
([ADR-0008](adr/0008-structured-failure-capture.md)):

| Field | Source |
|---|---|
| phase | `setup`, `call`, `teardown` or `collect` |
| exception | type (module-qualified unless builtin) and message |
| chain | `raise … from …` causes and implicit context, up to 3 |
| frames | file, line, function and source line of every frame from the first frame of the code under test (pytest's own frames are dropped) |
| locals | up to 12 local variables of the innermost repository or test frame, as `repr` (200 characters each; a failing `__repr__` is recorded as `<repr failed: …>`) |
| unexpected pass | a `xfail(strict=True)` test that passed |
| collection errors | pytest's report text. Import and syntax errors only exist as text, so TestMind extracts the last `E   Type: message` line and the location from it |

The plugin writes `/out/failures.json`. TestMind reads it back as **untrusted input**:
- the file is size-capped (10 MB);
- its shape and string lengths are validated;
- paths are only mapped to repository, generated-test or external locations, never opened.

Suite runs also pass `--continue-on-collection-errors`. Without it, one test module that
fails to import stops pytest before any test runs, which hides every other result.

## Deterministic analysis

### Categories

| Category | Rule |
|---|---|
| `import_error` | `ImportError` / `ModuleNotFoundError`, at collection or at runtime |
| `syntax_error` | `SyntaxError` / `IndentationError` / `TabError` while collecting |
| `collection_error` | any other error while collecting |
| `fixture_error` | failure in the `setup` or `teardown` phase |
| `exception_in_library` | the innermost frame is outside the repository (standard library or an installed package) |
| `exception_in_code` | raised in the repository's production code (including an `assert` there) |
| `assertion_failure` | an `AssertionError` raised by a test |
| `exception_in_test` | any other exception raised by test code |
| `unexpected_pass` | `xfail(strict=True)` passed |

A frame is **production**, **test** (repository test files, `conftest.py`, anything under
`tests/` or `test/`), **generated** (TestMind's generated modules) or **external**.

### Grouping

Failures with the same cause form one group. The group id is a hash of the signature, so it
stays stable across executions:

| Category | Signature |
|---|---|
| exceptions, fixture errors | category, exception type, innermost repository/test frame (file and line) |
| assertions | category, file and line of the failing `assert` |
| import errors | category, exception type, missing module (or the message) |
| collection and syntax errors | category, exception type, location, test module |
| unexpected passes | the test itself |

Example from [`examples/failing_suite`](../examples/failing_suite): four tests (three project
tests and one generated test) fail with `ZeroDivisionError` at `shop/stock.py:5`. They are
**one** group, not four.

### Linking and evidence

For each group the analyzer records:
- the frames, the crash frame and the frame the exception was actually raised in;
- the local variables and the exception chain;
- the **production function** it was raised in (from the code analysis), with its
  complexity, its findings and its coverage in the same execution.

Evidence is a list of facts, each with its source:

| Kind | Example |
|---|---|
| traceback | `ZeroDivisionError at shop/stock.py:5 in average: return total / count` |
| assertion | pytest's assertion introspection: `assert 'positive' == 'negative'` |
| locals | `Local variables in average: total = 10, count = 0` |
| chain | `Caused by ValueError: invalid literal for int() with base 10: 'x'` |
| finding | `Static analysis flagged the failing line: TM040 (medium, confidence 0.5): 'count' is used as a divisor without being validated (possible ZeroDivisionError)` |
| dependency | `'yaml' is not part of the repository and no dependency with that name is declared …` |
| repetition | `4 tests fail the same way at the same place (1 of them generated)` |

### Blame decided by the facts

Only where the facts leave no doubt:

| Situation | Blame |
|---|---|
| `ModuleNotFoundError` for a module that is not part of the repository | environment |
| `SyntaxError` in production code / in test code | code / test |
| `xfail(strict=True)` that passed: the documented failure no longer happens | test (the marker is out of date) |

Every other group has `blame: null` until an AI analysis is requested. Failed tests without
structured data are listed as `unanalysed_tests` and never guessed. This happens after a
timeout, for example, because pytest never finishes and never writes the file.

## AI analysis (on request)

`POST /api/v1/test-executions/{id}/failure-analyses` queues a job. The worker sends each
undecided group to the LLM, up to `TESTMIND_FAILURE_ANALYSIS_MAX_GROUPS` (default 10), largest
group first. Decided groups are reported as `deterministic` and the model is not asked about
them. Groups over the limit are reported as `skipped`.

**What the model sees:**
- the category;
- the exception and message;
- the failing tests;
- the evidence above;
- the traceback;
- the locals;
- the linked function;
- **numbered source excerpts**: ±3 lines around every repository and test frame, and the
  whole failing function (up to 60 lines).

Sources of generated tests come from the generation, because they are not in the repository.
The prompts are versioned files (`failure_analysis_system.v1.md`, `analyze_failure.v1.md`,
`repair_failure_analysis.v1.md`) and treat code and messages as data.

**What the model must return:** JSON with `blame` (`code`, `test` or `environment`), a
`root_cause`, `evidence` (path, line, explanation), a `suggested_fix` and a `confidence`.

**What TestMind checks:**
- **Shape.** The JSON is validated. An invalid reply gets one repair request with the exact
  problems. After that the group is `llm_error`.
- **Evidence.** Each reference is `verified` only if that file and line were part of the
  excerpts the model was shown. A line that does not exist, a file that was not shown, or a
  real line outside the excerpts is marked unverified.
- **Confidence.** `confidence = model confidence × verified / cited`. Without any cited
  evidence it is capped at 0.3. The model's own number is kept as `model_confidence`, and a
  note explains every reduction.

Verified with the real sandbox and database on `examples/failing_suite` (scripted model, no
API key available). The model's answer cited two real lines and one invented line, so the
confidence 0.85 became 0.57 and the invented line was marked unverified
(`tests/sandbox/test_failure_analysis_end_to_end.py`). **A live call to the Anthropic API has
not been made yet.**

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/test-executions/{id}/failures` | Counts, categories, groups decided by the facts, unanalysed tests, notes. `404` until the execution has run tests |
| `GET` | `/api/v1/test-executions/{id}/failures/groups` | Groups, largest first. `category` filter, pagination |
| `GET` | `/api/v1/test-executions/{id}/failures/groups/{group_id}` | One group |
| `POST` | `/api/v1/test-executions/{id}/failure-analyses` | Queue an AI analysis (`202`). `409` without failure groups, `503 llm_not_configured` without an LLM |
| `GET` | `/api/v1/test-executions/{id}/failure-analyses` · `/api/v1/failure-analyses/{id}` | Status and hypotheses |

## Limits

- The plugin records at most 500 failures per run and 30 frames per failure. Further
  failures are counted in a note.
- Locals are `repr`s from inside the sandbox and are shown to the LLM, like the source code.
  Code that keeps secrets in local variables of a failing frame would expose them to the
  configured LLM provider.
- An `assert` in a test that calls production code can still be the code's fault, for
  example `assert price(10) == 9` failing because `price` is wrong. The deterministic step
  does not guess this. It is exactly what the AI step is for.
