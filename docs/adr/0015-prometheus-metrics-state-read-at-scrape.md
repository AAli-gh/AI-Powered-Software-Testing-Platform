# ADR-0015: Prometheus metrics, with the platform's state read at scrape time

**Status:** Accepted

## Context

Until Phase 14, TestMind reported its health through logs and the `/ready` probe. An
operator could not answer the questions that matter while it runs:
- Are jobs waiting?
- Is a job stuck?
- Are tasks failing?
- Is the sandbox timing out?
- Is the API slow?
- How many LLM tokens is it using?

Two properties of TestMind shape the design:

1. **The state that matters is already in PostgreSQL.** Every job is a row with a status,
   created and started times. Counting these in the process that changes them (increment on
   queue, decrement on finish) would drift. It would be wrong after a crash, a restart, or
   a job reaped at start-up, and different in each API process.
2. **The worker is a prefork pool.** Tasks run in child processes. A metric incremented in a
   child is invisible to an HTTP server in the parent.

## Decision

- **Prometheus, pull model.** It is the standard for this kind of service, with a query
  language and alert rules. `prometheus_client` is the one new runtime dependency.
- **Events are counted where they happen:**
  - HTTP requests, by route template, in the API middleware;
  - task outcomes, through Celery's `task_prerun` / `task_postrun` signals;
  - sandbox runs, in `DockerSandbox`;
  - LLM calls and tokens, in a `MeteredProvider` wrapper, like redaction.
- **State is read when Prometheus scrapes.** `/metrics` runs one `UNION ALL` query over the
  ten job tables and one Redis `LLEN` per priority list. That gives the jobs by status, the
  oldest queued and running job, and the queue length. It cannot drift, and every API process
  gives the same answer. A dependency that cannot be read is reported as
  `testmind_dependency_up 0`, and the scrape itself still succeeds.
- **The worker uses the client's multiprocess mode.** Pool processes write samples to files in
  `PROMETHEUS_MULTIPROC_DIR`, and the main process serves their sum. Every metric has labels,
  so a process that only imports them (the health check) creates no files.
- **Bounded labels only:** route templates (`unmatched` for unknown paths), task names, fixed
  outcome sets, and exit codes folded into a known set.
- `/metrics` needs the API token when one is set. It is not in the OpenAPI schema.
- **Alert rules live in the repository with unit tests** (`promtool test rules`), run in CI.
  Prometheus runs as an opt-in compose overlay. Routing alerts to people is left to each
  deployment.

## Consequences

- A scrape costs one database query and one Redis round trip: about 11 ms per scrape
  ([performance.md](../performance.md)).
- The API must run as one process per container for its HTTP metrics to be correct, which is
  how the image runs it. It is scaled with more containers.
- A task killed by its hard time limit dies with its process and is not counted as a task.
  Its job is still counted as failed, from the database.
- No Grafana dashboards and no tracing: fewer moving parts, stated in
  [monitoring.md](../monitoring.md).
