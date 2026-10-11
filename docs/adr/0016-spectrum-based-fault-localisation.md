# ADR-0016: Localise faults with the program spectrum, with rules fixed before measuring

**Status:** Accepted

## Context

The evaluation on BugsInPy ([evaluation.md](../evaluation.md)) showed that failure analysis
blamed a function only through the traceback. When a bug only makes a test's assertion fail,
the traceback ends in the test: 23 of 39 scored bugs got no suspect at all. TestMind is meant
to find bugs without an external LLM, so the fix had to be deterministic.

Spectrum-based fault localisation (SBFL) is the standard answer in the literature. A function
that the failing tests execute, and the passing tests do not, is suspicious. It needs to know
which test executed which function, which coverage.py records as per-test contexts. TestMind
already used them for regression test selection and mutation testing.

## Decision

1. **Every suite execution records per-test coverage.** Its spectrum is stored with the
   execution in a compact form: each test id once, each function as a list of test indexes.
2. **Ochiai** is the formula. It is the reference formula of the SBFL literature, and it was
   chosen before anything was measured. DStar and Tarantula are implemented for comparison
   only.
3. **One rule combines the signals.** The production frames of the failure's traceback come
   first, innermost first. Then come the other functions the failing tests executed, by score.
   Ties get the worst rank of their tie.
4. **Each failure group is ranked on its own.** Its failing tests are compared against every
   passing test, because a group is one failure and possibly one bug. An endpoint ranks any
   set of failing tests from the stored spectrum. It works without a traceback, so it also
   covers failures beyond the failure plugin's cap.
5. **The confidence is reported.** It is high, medium, low or none, from the tie at the top.
   A long tie is shown as low confidence, not hidden behind an arbitrary order.
6. **Nothing was tuned on the evaluation's bugs.** Learned ranking is a later phase, trained
   and measured on separate data (bugs injected by mutation, split by project).

## Consequences

- Assertion-only bugs are now localised. The real-sandbox test
  `tests/sandbox/test_fault_localization.py` checks one, and the results on BugsInPy are in
  [evaluation.md](../evaluation.md#fault-localisation).
- Per-test contexts slow coverage down, and the spectrum adds a JSONB column to every
  execution. The cost is measured in [evaluation.md](../evaluation.md#fault-localisation).
- Test ids follow TestMind's `file::name` form. Two tests of the same name in different
  classes of one file are merged in the spectrum, the same limit as in the test report.
- SBFL can only rank code that the failing tests execute. A bug that is missing code (an
  absent check) is localised to the function where the check belongs, at best.
