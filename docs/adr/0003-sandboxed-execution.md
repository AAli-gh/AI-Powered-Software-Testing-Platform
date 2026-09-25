# ADR-0003: Execute repository code only in sandbox containers

**Status:** Accepted. Implementation starts in Phase 5 (test runner) and is hardened in
Phase 13.

## Context

From Phase 5, TestMind runs other people's code: their test suites, generated tests and
mutants. That code can read secrets, open network connections, fork-bomb, fill the disk or
attempt container escapes.

## Decision

- Repository code **never** runs inside the `api` or `worker` containers.
- Each execution runs in a fresh, disposable **sandbox container** created from a
  per-project runner image, with:
  - CPU, memory and pids limits, and a wall-clock timeout enforced from outside;
  - **no network** by default (`--network none`), with an explicit allow-list for dependency
    installation if required;
  - a read-only copy of the repository plus a writable scratch `tmpfs`;
  - a non-root user, all capabilities dropped, `no-new-privileges` and a seccomp profile;
  - no platform secrets in its environment.
- Results leave the sandbox only as structured files (JUnit XML / JSON / coverage data) that
  the worker parses as untrusted input.

### Open question: how the worker starts sandboxes

Mounting `/var/run/docker.sock` into the worker is equivalent to giving it root on the host.
The options will be evaluated in Phase 5:

| Option | Trade-off |
|---|---|
| Docker socket behind a restricting proxy (e.g. docker-socket-proxy) | Simple and widely used, but the proxy must allow container creation, which is still powerful |
| Rootless Docker / Podman for sandboxes | Removes host-root risk, but has operational differences |
| gVisor (`runsc`) runtime for sandboxes | Strong kernel isolation, with some performance and compatibility cost |
| Kubernetes Jobs | Natural for production scale, but heavy for local development |

## Consequences

- Test execution is slower than in-process runs (container start-up). This is acceptable for
  correctness and safety.
- Django projects that need a database will get a sidecar database container on the sandbox's
  private network.
