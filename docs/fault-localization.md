# Fault localisation

When tests fail, which function holds the bug? TestMind answers with a **ranked list of
suspect functions** and a **confidence**, for every failure group of every execution. It is
deterministic, uses no LLM, and is measured on real bugs
([evaluation.md](evaluation.md#fault-localisation)).

Code: `src/testmind/fault_localization/` (spectrum, formulas, ranking) and
`fault_localization/failures.py` (one ranking per failure group). The design decision is in
[ADR-0016](adr/0016-spectrum-based-fault-localisation.md).

## Two kinds of evidence

| Evidence | What it says | Strength | Limit |
|---|---|---|---|
| **Traceback** | The exception was raised in this function, or passed through it | Direct: the function ran when the test failed | Nothing to say when only the test's assertion fails: the traceback ends in the test |
| **Spectrum** | Which tests executed which function | Works for any failure, assertions included | Statistical: a function that every test runs cannot be told apart |

The **spectrum** comes from coverage.py's per-test contexts, which every suite execution
records. It is stored with the execution in a compact form: each test id once, each function
as a list of test indexes.

## The ranking

Fixed before it was measured, and not tuned on the evaluation's bugs:

1. **Traceback functions first**, innermost first. A frame proves its function ran, so it is
   a suspect even when coverage missed it (a project that installs its own tracer hides its
   code from coverage.py; the ranking says so in a note).
2. **Then every other function the failing tests executed**, by **Ochiai** score, highest
   first:

   ```text
   ochiai = failed / sqrt(total_failed × (failed + passed))
   ```

   `failed` and `passed` count the failing and passing tests that executed the function. A
   function that only failing tests execute scores 1. A function that no failing test
   executed is not a suspect.
3. **Ties get the worst rank of their tie.** Three functions sharing the top score are all
   ranked 3, so a tie never looks better than it is.

Each failure group is ranked on its own: its failing tests against every passing test.

## Confidence

| Confidence | When |
|---|---|
| `high` | The top suspect is in the traceback, or the only function with the top score |
| `medium` | Two or three functions share the top score |
| `low` | More share it: the tests cannot tell them apart |
| `none` | The failing tests executed no production function |

A low confidence is information too: the tests do not tell the suspects apart. More focused
tests would.

## Where to see it

- **Dashboard**: the execution's *Failures* tab shows each group's suspects, scores, the
  failing and passing tests that executed each one, the traceback marker and the confidence.
- **API**:
  - every failure group (`GET /test-executions/{id}/failures/groups`) has `localization`;
  - `GET /test-executions/{id}/failures/suspects?test=…&test=…` ranks any failing tests of the
    execution (all of them by default) from the stored spectrum, without a traceback. It also
    covers failures beyond the failure plugin's cap of 500 recorded failures.

## Example

`tests/sandbox/test_fault_localization.py` runs this project in the real sandbox:

```python
def apply_discount(price, percent):
    return price + price * percent / 100  # the bug: + instead of -
```

Two tests fail on their assertions, so the traceback blames nothing. Three tests pass. The
spectrum ranks `apply_discount` first, with high confidence: both failing tests executed it,
and only one of the passing tests did.

## A learned ranker (evaluated, not adopted)

The ranking above is a fixed rule. A fair question is whether a model trained on past bugs
would rank better. TestMind can answer that without guessing, so the ranking's own code takes
a pluggable **scorer** (`ranking.localize(..., scorer=…)`); the default is Ochiai, and
`fault_localization/learned.py` offers a learned alternative: a plain logistic-regression model
(pure Python, no external library) over eight per-function features (the three spectrum
formulas, failing/passing ratios, a "only failing tests ran it" flag, test support, and a
traceback flag). `fault_localization/training.py` fits it from labelled examples.

The learned ranker is **off by default and is not wired into any suite run.** It earns its
place only if it is measurably better, and on the evidence gathered so far it is not:

- `benchmarks/fault_localization/` injects real single-line bugs into a small bundled corpus
  (`corpus/calc`, `corpus/textutil`, `corpus/pricing`), runs each mutant under pytest with
  coverage contexts, and records the real spectrum, failing/passing tests and known faulty
  function. This produced **18 labelled bugs** — reproducible anywhere, no Docker, no network
  (`uv run python -m benchmarks.fault_localization generate`).
- Evaluated **leave-one-project-out** (train on two projects, score the third), so the model is
  never tested on a project it trained on
  (`uv run python -m benchmarks.fault_localization evaluate`):

  | ranker | top-1 | top-5 | MRR |
  |---|---|---|---|
  | deterministic Ochiai rule | 18 / 18 | 18 / 18 | 1.00 |
  | learned (logistic regression) | 18 / 18 | 18 / 18 | 1.00 |

This is a **null result, reported as one.** On these bugs the deterministic rule already puts
the faulty function first every time, so there is no headroom for the model to win — it ties,
it does not improve. The honest conclusion is that the deterministic rule stays the default;
the learned path exists so the comparison can be re-run on a larger, harder corpus later, where
a difference could actually show. The corpus is deliberately small and its bugs are
single-line operator swaps, so the ceiling says as much about the corpus as about either
ranker. The pure comparison logic is unit-tested in
`tests/unit/benchmarks/test_fault_localization_evaluate.py`; the ranker engine itself in
`tests/unit/fault_localization/`.

## Limits

- **Only executed code can be ranked.** A missing check is found, at best, in the function
  where it belongs.
- **Weak tests weaken it.** When the passing and failing tests execute the same functions, the
  scores tie and the confidence says `low`.
- **Test ids are `file::name`**, as in the rest of TestMind. Two tests of the same name in
  different classes of one file are one id in the spectrum.
- **Per-test coverage costs time.** The overhead is measured in
  [evaluation.md](evaluation.md#fault-localisation).
