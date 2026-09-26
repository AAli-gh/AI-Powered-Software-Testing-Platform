# ADR-0006: The LLM proposes, deterministic tools decide

**Status:** Accepted

## Context

Phase 4 uses an LLM to write tests. LLM output can be wrong, can be "fixed" by weakening
assertions, can be influenced by instructions hidden in the analysed code (prompt
injection), and can contain code that is harmful to run.

## Decision

1. **No self-grading.** Acceptance is decided only by static checks (AST) and a real pytest
   run in the sandbox. The model's own claims ("these tests pass") are never used.
2. **Bounded loop.** Generate → validate → run → fix happens at most
   `TESTMIND_GENERATION_MAX_ITERATIONS` times per module. Failure is a normal, reported
   outcome.
3. **Bugs are surfaced, not hidden.** Tests that fail because of a genuine defect are kept
   with `xfail(reason="possible bug: …", strict=True)` and reported as suspected bugs.
4. **Execution only in the sandbox** ([ADR-0003](0003-sandboxed-execution.md)). The
   analysed code is treated as untrusted data in the prompt, and a successful injection
   can at worst produce tests that run inside the sandbox.
5. **Provider-neutral interface**, one vendor implementation (Anthropic) to start with.
   Credentials stay in the vendor SDK's own environment variables.
6. **Traceability.** Every result records the prompt version, the model that answered and
   token usage.

## Consequences

- Generation is slower and costs more tokens than a single call, but the tests it stores
  are known to run.
- Without Docker access the loop degrades explicitly to `unverified` results instead of
  pretending success.
