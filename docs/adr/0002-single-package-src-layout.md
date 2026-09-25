# ADR-0002: Single `testmind` package with `src/` layout

**Status:** Accepted

## Context

The initial brief sketched top-level directories `backend/`, `ai_engine/`, `test_engine/`,
`analyzers/` and `github/`. As separate top-level Python directories, each would need its own
packaging or `sys.path` manipulation to import the others. A top-level `github` package would
also shadow PyGithub (`import github`).

## Decision

One installable package, `testmind`, under `src/`, with the same logical modules as
sub-packages: `testmind.analyzers`, `testmind.test_engine`, `testmind.ai_engine` and
`testmind.integrations.github`, each created in the phase that implements it. The frontend stays
a separate top-level `frontend/` app.

## Consequences

- Plain absolute imports everywhere, one version, one lock file, one Docker image with two
  targets (`api`, `worker`).
- The `src/` layout guarantees tests run against the installed package, not the working tree by
  accident.
- If a module ever needs independent deployment (for example a slim sandbox-runner image), it
  can be split out along the existing package boundaries.
