"""Worker metrics: recorded in every pool process, served by the worker's main process.

Celery's prefork pool runs tasks in child processes. With PROMETHEUS_MULTIPROC_DIR set, each
process writes its samples to a file there; the main process serves the sum of all files on
TESTMIND_WORKER_METRICS_PORT. A task killed by its hard time limit takes its process with it
and is not counted here: it is still visible as a failed job in the API's metrics.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, multiprocess, start_http_server

from testmind.core.metrics import TASK_DURATION, TASKS

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

_started: dict[str, float] = {}


def task_started(task_id: str, **_: Any) -> None:
    _started[task_id] = time.monotonic()


def task_finished(task_id: str, task: Any, retval: Any = None, state: str | None = None,
                  **_: Any) -> None:  # fmt: skip
    """The outcome is the job's final status as the task returned it; `missing` when the job
    no longer existed, `error` when the task raised."""
    started = _started.pop(task_id, None)
    if state != "SUCCESS":
        outcome = "error"
    elif isinstance(retval, str):
        outcome = retval
    else:
        outcome = "missing"
    TASKS.labels(task.name, outcome).inc()
    if started is not None:
        TASK_DURATION.labels(task.name).observe(time.monotonic() - started)


def serve(port: int, host: str = "0.0.0.0") -> Any:  # noqa: S104  # nosec B104 - container
    """Start serving every process's samples. Called once, in the main process, before the
    pool starts; samples left by an earlier worker in the directory are removed first."""
    directory = os.environ.get(MULTIPROC_ENV)
    if not directory:
        raise RuntimeError(
            f"TESTMIND_WORKER_METRICS_PORT needs {MULTIPROC_ENV}: without it, the samples of "
            "the pool's processes would be lost"
        )
    path = Path(directory)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, mode=0o700)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry, path=str(path))  # type: ignore[no-untyped-call]
    server, _ = start_http_server(port, addr=host, registry=registry)
    return server
