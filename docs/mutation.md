# Mutation testing

Line coverage says which code the tests **run**. Mutation testing asks whether they
**check** it. Phase 9 makes small, deliberate bugs (mutants) in the production code and runs
the tests against each one:
- a mutant is **killed** if a test fails;
- it **survives** if every test that runs the line still passes.

A surviving mutant is a concrete, reproducible gap. For example: "`weight > 10` could be
`weight >= 10` and no test would notice."

Code: `src/testmind/mutation_engine/` (operators, engine, suggester) and the in-sandbox driver
[`docker/testmind_mutate.py`](../docker/testmind_mutate.py).

## Steps

```text
baseline run with per-test coverage → exclude tests that already fail
→ generate mutants (riskiest functions first, up to a limit)
→ plan: the tests that executed each mutant's lines at baseline
→ one sandbox container runs every mutant → scores per file / function / run
```

1. **Baseline.** The whole suite runs once with coverage.py's dynamic contexts (as in
   [regression analysis](regression.md#2-per-test-coverage)). This records which test executed
   each line. Tests that fail without any mutation are excluded, because they would "kill"
   every mutant. The report lists them.
2. **Generation.** Mutants are generated deterministically from the AST (see below).
   Functions are ordered by risk (cyclomatic complexity + 2 × static-analysis findings, the
   same weight as coverage gaps), and only the first `max_mutants` are run. The report says
   how many were generated and how many were run.
3. **Planning.** Each mutant runs only the tests that executed one of its lines at baseline.
   A mutant that no test executes is `no_coverage` and is **not run**, because the answer is
   already known.
4. **Execution.** Every mutant runs in **one** sandbox container
   ([ADR-0010](adr/0010-single-container-mutation-driver.md)). For each mutant, the driver:
   - checks that the bytes it replaces are the expected original text;
   - writes the mutant;
   - runs its tests with `pytest -x`, stopping at the first failure;
   - restores the file.

   Each mutant has its own timeout: `5 s + 5 × the baseline duration of its tests`, capped by
   `TESTMIND_MUTATION_MUTANT_TIMEOUT_SECONDS`. When it expires, the whole process group is
   killed. The container's timeout is the sum of those plus 60 s, capped by
   `TESTMIND_MUTATION_JOB_TIMEOUT_SECONDS`.

## Operators

| Operator | Change |
|---|---|
| `arithmetic` | `+` ↔ `-`, `*` ↔ `/`, `//` → `/`, `%` → `*`, `**` → `*` |
| `comparison` | `<` ↔ `<=`, `>` ↔ `>=`, `==` ↔ `!=`, `is` ↔ `is not`, `in` ↔ `not in` |
| `boolean` | `and` ↔ `or` |
| `negation` | `not x` → `x` |
| `condition` | `if c:` → `if not (c):`. Skipped when `c` is a single `==`, `!=`, `is`, `in` (or their negation) comparison, because the comparison operator already produces that mutant |
| `constant` | `n` → `n + 1` for integers, `True` ↔ `False` |
| `return` | `return x` → `return None` (not when `x` is already `None`) |

Each mutant is an exact byte range of the file and its replacement, so the driver can check
the file before it changes anything. Mutants are kept only if they are useful and valid:
- **Only inside functions.** Module-level code runs at import, so a mutant there changes every
  test at once and says little about any of them.
- **Never in text that cannot change behaviour:** docstrings, type annotations (arguments,
  return types, annotated assignments), decorators and f-string parts. String concatenation
  and `%` formatting are not arithmetic.
- **Must still compile.** Otherwise the mutant is dropped.
- **Must be distinct.** Two mutants that produce the same file are generated once.

## Verdicts and scores

| Status | Meaning | Counts as |
|---|---|---|
| `killed` | A test failed, or the mutant broke collection (`ERROR collecting …`) | detected |
| `timeout` | The tests hung, usually an endless loop | detected |
| `survived` | Every test that runs the line passed | not detected |
| `no_coverage` | No test executes the line, so it was not run | not detected |
| `error` | No verdict: the source did not match, the driver crashed, the run timed out as a whole, or no sandbox | excluded |

- **score** = detected / (detected + survived + no_coverage). This is how well the suite
  protects the code.
- **covered_score** = detected / (detected + survived). This is how good the tests that do run
  are.

The run, each file and each function get both scores. `GET …/functions` lists the weakest
functions first.

A killed mutant names the test that killed it (from pytest's short summary). An `error`
carries the tail of pytest's output as its detail, for example when pytest exits with a
usage error that is not a collection error.

## AI test suggestions (on request)

For surviving mutants (riskiest first, `TESTMIND_MUTATION_MAX_SUGGESTIONS` at most), the model
gets:
- the original and mutated code;
- the line in context;
- the module source;
- how many existing tests run the line.

It writes a test module (prompts `kill_mutant.v1`, `fix_mutant_test.v1`). A suggestion is
**accepted only if the sandbox shows both**:
1. The test **passes on the original code**, in a normal sandbox run.
2. The test **kills the mutant**, in a driver run with the candidate test added to the
   working copy.

Before that, the same static checks as generated tests apply: the code parses, contains
tests, imports the module under test, and uses no network, process or file-deleting calls.
If a check fails, the concrete problem goes back to the model once. Examples of the problem
sent back:
- "does not pass on the ORIGINAL code: …";
- "ALSO passes when `x` is changed to `y`".

The statuses are:
- `accepted`;
- `rejected` (no candidate did both);
- `llm_error`;
- `sandbox_error`.

Every attempt is recorded with its outcome. The model's own claims are never used as a
verdict.

The job needs an LLM provider (`503 llm_not_configured` otherwise) and at least one surviving
mutant (`409`). Accepted tests are **returned, not written** into the repository.

## Verified example

[`examples/shipping_rates`](../examples/shipping_rates) has **100 % line coverage** and weak
tests. In the real sandbox (`tests/sandbox/test_mutation_engine.py`) it gives exactly the
result documented in
[`EXPECTED_MUTATION.md`](../examples/shipping_rates/EXPECTED_MUTATION.md): **20 mutants, 12
killed, 8 survived, score 60 %**. Every survivor is an untested boundary (`weight >= 10`,
`total > 100`, …), except one mutant that is equivalent for the tested inputs
(`round(cost, 3)`).

With a scripted model, `tests/sandbox/test_mutant_suggestions.py` checks the acceptance rules
against the real sandbox:
- A test that passes on the original but does not kill the mutant is rejected
  (`mutant_survives`). A better test on the next attempt is then accepted.
- A test that fails on the original code is rejected (`fails_on_original`).

The Celery path runs end to end in `tests/sandbox/test_mutation_end_to_end.py`.

## Limits

- Python only. First-order mutants only (one change per mutant).
- **Equivalent mutants** (changes that do not alter behaviour for any input) cannot be
  detected in general. They show up as survivors, as the example documents.
- Targeting relies on per-test coverage. Code executed only at import (module level) has no
  per-test attribution, and mutants are never generated there.
- Mutants run one after another in one container. Large projects need a `max_mutants` limit,
  and the report says how many were skipped.
- A mutant that changes shared state outside the working copy (for example, a file under
  `/tmp` that a later test reads) can affect the next mutant. The working copy itself is
  restored after each mutant.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TESTMIND_MUTATION_MAX_MUTANTS` | 200 | Server cap on mutants per run (a request's `max_mutants` cannot exceed it) |
| `TESTMIND_MUTATION_MUTANT_TIMEOUT_SECONDS` | 60 | Cap on one mutant's test run |
| `TESTMIND_MUTATION_JOB_TIMEOUT_SECONDS` | 1800 | Cap on the whole mutation container |
| `TESTMIND_MUTATION_MAX_SUGGESTIONS` | 10 | Surviving mutants per suggestion job |

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/runs/{id}/mutation-runs` | Body (optional): `{"max_mutants": 1-5000}` (the server cap wins). `202`; `404`; `409` run not succeeded; `503 dispatch_failed` |
| `GET` | `/api/v1/runs/{id}/mutation-runs` · `/api/v1/mutation-runs/{id}` | Status, `score`, `summary` (baseline outcome, generated, totals, notes) |
| `GET` | `/api/v1/mutation-runs/{id}/mutants` | `status`, `path`, `limit` ≤ 500, `offset`. Each mutant: location, operator, original, replacement, status, tests run, killed by |
| `GET` | `/api/v1/mutation-runs/{id}/functions` | Scores per function, weakest first |
| `POST` | `/api/v1/mutation-runs/{id}/test-suggestions` | Queue AI tests for survivors. `202`; `409` no survivors; `503 llm_not_configured` / `dispatch_failed` |
| `GET` | `/api/v1/mutation-runs/{id}/test-suggestions` · `/api/v1/test-suggestions/{id}` | Status and `result.suggestions[]`: mutant, status, test path, code, attempts |
