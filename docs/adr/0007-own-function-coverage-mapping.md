# ADR-0007: TestMind maps coverage onto functions itself

**Status:** Accepted

## Context

Phase 6 needs coverage per function, to find weak spots and to guide test generation.
coverage.py measures lines and branches. Since version 7.5 its JSON report also contains
function "regions", but:

- the tests run in a runner image that also contains the **project's** dependencies, and a
  project may pin an older coverage.py, which has no regions;
- region names differ from the qualnames used everywhere else in TestMind
  (`outer.inner` vs `outer.<locals>.inner`);
- TestMind already has exact function boundaries, complexity and findings from the code
  analysis (Phase 3).

## Decision

1. coverage.py is the **only** source of *which lines and branches ran*. TestMind never
   estimates execution.
2. TestMind computes function coverage from those lines and the Phase 3 boundaries. A
   function owns the statements after its `def` line up to its end, minus nested function
   bodies, plus the branches that start from those statements.
3. The mapping is **verified against coverage.py's own regions** in the sandbox test suite, on
   every example repository and on a module built to stress scoping rules. Every difference
   must be understood and asserted explicitly. Today there is exactly one: the decorator line
   of a nested decorated function belongs to the enclosing function in TestMind (it runs when
   that function is called) and to the module in coverage.py's regions.
4. TestMind passes its own coverage config (`--cov-config`), so a project's settings cannot
   change the measurement or fail the run.

## Consequences

- Function coverage works with any coverage.py version that writes line and branch data.
- Function results use the same qualnames as the code analysis, so gaps can be joined with
  complexity and findings, and with generation targets.
- A pinned coverage.py version in the sandbox image keeps the golden numbers in the tests
  deterministic. Upgrading it means re-running the agreement tests.
