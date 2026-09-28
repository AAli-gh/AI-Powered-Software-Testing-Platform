# ADR-0014: The containerised worker reaches Docker only through a policy-enforcing proxy

**Status:** Accepted

## Context

The worker runs repository tests in Docker sandbox containers ([ADR-0003](0003-sandboxed-execution.md)).
Until Phase 13 this worked only when the worker ran on a Docker host (`make worker`, CI). The
compose stack's worker had no Docker access, and reported `sandbox_unavailable`.

Mounting the Docker socket into the worker would fix that, and would also give anyone who
compromises the worker root on the host. The worker handles hostile input (repositories,
archives, LLM output). The alternatives:

- **An off-the-shelf socket proxy** filters by endpoint class (e.g. "containers: allowed").
  That still lets a client create a privileged container that mounts `/`, so it is not a
  boundary.
- **Rootless Docker or a user-space kernel (gVisor)** hardens the daemon or the sandbox. It
  does not limit what the worker may ask the daemon for, and it changes how TestMind is
  installed.
- **A narrow sandbox service with its own RPC** would move the whole runner behind a new API:
  a large rewrite of working, tested code.

## Decision

A small proxy, `testmind.sandbox_proxy`, is the only holder of the socket. It speaks the Docker
API, so the worker keeps using the ordinary Docker CLI and `DockerSandbox` is unchanged. It
parses every request and applies a **policy written for TestMind's sandbox**:

- **Default deny.** Only the endpoints the sandbox uses are allowed.
- **Container creation.** Every field of the create body is checked (network, capabilities,
  user, read-only root, limits, bind-mount sources and targets, tmpfs). Unknown fields fail
  closed. The proxy labels the container and forces TestMind's seccomp profile on it.
- **Other container operations** only on containers carrying that label.
- **Image builds** only from TestMind's runner Dockerfile template with plain requirements,
  with the classic builder.
- **One request per connection.** Bytes after a request are never forwarded.

Bind-mount sources are host paths, so the directories the sandbox mounts are mounted at the
same paths on the host, in the worker and, read-only, in the proxy. The proxy can then refuse
sources that go through symlinks.

Deployment is opt-in (`docker-compose.sandbox.yml`, `make up-sandbox`), and CI runs its Docker
job on that stack. The smoke test there fails if the sandbox does not really run.

## Consequences

- The compose stack runs tests, coverage, mutation testing and API probes in the sandbox,
  verified end to end in CI.
- The proxy is security-critical code. It is kept small (standard library only), covered
  line for line, and tested against the real daemon with the attacks it must refuse.
- A new sandbox feature that needs another Docker capability must be added to the policy
  explicitly. That is the intended friction.
- The design does not protect against a daemon or kernel vulnerability reachable from inside
  a sandbox container. That is what the seccomp profile, and later a user-space kernel, are
  for.
