# ADR-0010: Run all mutants of a job in one sandbox container

**Status:** Accepted

## Context

Mutation testing (Phase 9) runs the tests once per mutant, often hundreds of times. Every
execution of repository code must happen in the sandbox (ADR-0003): no network, unprivileged,
read-only root, resource limits. Starting a new container per mutant costs one to two seconds
each. That is often more than the tests themselves, and a 200-mutant job would spend most of
its time starting containers.

## Decision

1. One mutation job runs in **one** sandbox container, with the same isolation flags as every
   other test run. Only the tmpfs is larger (256 MB), because it holds the working copy.
2. A small driver baked into the sandbox image (`docker/testmind_mutate.py`, not part of the
   `testmind` package) copies the read-only repository into the tmpfs once. For each mutant it
   then:
   - verifies the original bytes;
   - writes the mutant;
   - runs only that mutant's tests (`pytest -x`, in its own process group, with its own
     timeout);
   - restores the file.
3. TestMind decides everything: which mutants, which tests, which timeouts. The driver only
   executes the job file and writes one JSON result. The result is validated like any other
   untrusted output. If it is missing or invalid, every mutant becomes `error` with a note,
   never a guessed verdict.
4. A whole-job timeout (the sum of the per-mutant timeouts plus 60 s, capped) bounds the
   container.
5. AI test suggestions reuse the same driver to check "kills the mutant", with the candidate
   test passed as an extra file.

## Consequences

- One container per job instead of one per mutant. Dozens of mutants take seconds.
- The mutants of a job are not isolated from each other beyond the file restore and the
  process-group kill. A mutant that writes shared state outside the working copy (for example
  a file in `/tmp`) could affect later mutants. This is documented as a limit. It stays inside
  the one sandbox and cannot reach the host.
- The driver is part of the sandbox image, so a change to it needs the image rebuilt
  (`make sandbox-image`). The runner image tag already follows the base image's content id.
