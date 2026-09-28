"""Prometheus metrics recorded by the API and the worker (docs/monitoring.md).

Label values are always taken from small, fixed sets (route templates, task names, outcome
names), never from user input, so the number of series stays bounded. Every metric has labels:
with them, a process that only imports this module (e.g. the worker's health check) writes no
samples.

The worker runs Celery's prefork pool: with PROMETHEUS_MULTIPROC_DIR set, every process writes
its samples there and the worker's main process serves them together
(testmind.workers.metrics). The variable must be set before this module is imported.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

# Seconds. HTTP requests are short; jobs and sandbox runs take seconds to tens of minutes.
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
JOB_BUCKETS = (1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1200.0, 1800.0, 3600.0)
LLM_BUCKETS = (0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 60.0, 120.0, 300.0)

# --------------------------------------------------------------------------------- API
HTTP_REQUESTS = Counter(
    "testmind_http_requests",
    "HTTP requests answered, by method, route template and status code",
    ["method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "testmind_http_request_duration_seconds",
    "Time to answer an HTTP request, by method and route template",
    ["method", "route"],
    buckets=HTTP_BUCKETS,
)

# ------------------------------------------------------------------------------ worker
TASKS = Counter(
    "testmind_tasks",
    "Worker tasks finished, by task and outcome (the job's final status, or `error`)",
    ["task", "outcome"],
)
TASK_DURATION = Histogram(
    "testmind_task_duration_seconds",
    "Time a worker task ran, by task",
    ["task"],
    buckets=JOB_BUCKETS,
)
SANDBOX_RUNS = Counter(
    "testmind_sandbox_runs",
    "Sandbox containers run, by kind (pytest, mutation) and result (exit code or timeout)",
    ["kind", "result"],
)
SANDBOX_DURATION = Histogram(
    "testmind_sandbox_run_duration_seconds",
    "Time a sandbox container ran, by kind",
    ["kind"],
    buckets=JOB_BUCKETS,
)
LLM_REQUESTS = Counter(
    "testmind_llm_requests",
    "LLM completions requested, by outcome (ok, refused, error)",
    ["outcome"],
)
LLM_TOKENS = Counter(
    "testmind_llm_tokens",
    "LLM tokens used, by direction (input, output)",
    ["direction"],
)
LLM_DURATION = Histogram(
    "testmind_llm_request_duration_seconds",
    "Time an LLM completion took, by outcome",
    ["outcome"],
    buckets=LLM_BUCKETS,
)

# Exit codes that mean something (pytest's 0-5, Docker's 125-127, SIGKILL's 137); any other
# is reported as "other".
KNOWN_EXIT_CODES = frozenset({0, 1, 2, 3, 4, 5, 125, 126, 127, 137})


def sandbox_result(exit_code: int | None, *, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if exit_code in KNOWN_EXIT_CODES:
        return f"exit_{exit_code}"
    return "other"


def observe_sandbox(kind: str, seconds: float, exit_code: int | None, *, timed_out: bool) -> None:
    SANDBOX_RUNS.labels(kind, sandbox_result(exit_code, timed_out=timed_out)).inc()
    SANDBOX_DURATION.labels(kind).observe(seconds)
