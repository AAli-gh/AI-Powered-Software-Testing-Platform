"""Worker metrics: task outcomes through Celery's own signals, and samples written by several
real processes served together by one."""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery import Celery
from celery.signals import task_postrun, task_prerun
from prometheus_client import REGISTRY
from prometheus_client.parser import text_string_to_metric_families

from testmind.workers import metrics


@pytest.fixture
def celery_app() -> Iterator[Celery]:
    app = Celery("metrics-test")
    app.conf.task_always_eager = False
    task_prerun.connect(metrics.task_started)
    task_postrun.connect(metrics.task_finished)
    yield app
    task_prerun.disconnect(metrics.task_started)
    task_postrun.disconnect(metrics.task_finished)


def counted(task: str, outcome: str) -> float:
    labels = {"task": task, "outcome": outcome}
    return REGISTRY.get_sample_value("testmind_tasks_total", labels) or 0.0


def timed(task: str) -> float:
    return REGISTRY.get_sample_value("testmind_task_duration_seconds_count", {"task": task}) or 0


def test_task_outcomes_through_celery_signals(celery_app: Celery) -> None:
    results: list[object] = ["succeeded", None]

    @celery_app.task(name="test.metrics.job", shared=False)
    def job() -> object:
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    before = (counted("test.metrics.job", "succeeded"), counted("test.metrics.job", "missing"),
              counted("test.metrics.job", "error"), timed("test.metrics.job"))  # fmt: skip
    job.apply()  # the job ended as `succeeded`
    job.apply()  # the job no longer existed
    results.append(RuntimeError("bug"))
    job.apply()  # the task raised (apply stores the exception, does not raise)
    after = (counted("test.metrics.job", "succeeded"), counted("test.metrics.job", "missing"),
             counted("test.metrics.job", "error"), timed("test.metrics.job"))  # fmt: skip
    assert tuple(a - b for a, b in zip(after, before, strict=True)) == (1, 1, 1, 3)


def test_a_task_that_never_started_is_counted_without_a_duration() -> None:
    class Task:
        name = "test.metrics.orphan"

    before = (counted(Task.name, "failed"), timed(Task.name))
    metrics.task_finished("unknown-id", Task(), retval="failed", state="SUCCESS")
    assert (counted(Task.name, "failed") - before[0], timed(Task.name) - before[1]) == (1, 0)


def test_serving_needs_the_multiprocess_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(metrics.MULTIPROC_ENV, raising=False)
    with pytest.raises(RuntimeError, match="PROMETHEUS_MULTIPROC_DIR"):
        metrics.serve(0)


RECORD = """
from testmind.core.metrics import TASKS, observe_sandbox
TASKS.labels("testmind.test_execution.execute", "succeeded").inc()
observe_sandbox("pytest", 2.0, 1, timed_out=False)
"""


def test_samples_of_every_process_are_served_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = tmp_path / "metrics"
    directory.mkdir()
    (directory / "counter_999999.db").write_bytes(b"left by an earlier worker")
    monkeypatch.setenv(metrics.MULTIPROC_ENV, str(directory))
    server = metrics.serve(0, host="127.0.0.1")
    try:
        assert not (directory / "counter_999999.db").exists()
        env = {**os.environ, metrics.MULTIPROC_ENV: str(directory)}
        for _ in range(2):  # two pool processes
            subprocess.run([sys.executable, "-c", RECORD], env=env, check=True)  # noqa: S603
        url = f"http://127.0.0.1:{server.server_port}/metrics"
        with urllib.request.urlopen(url, timeout=5) as answer:  # nosec B310
            text = answer.read().decode()
    finally:
        server.shutdown()
        server.server_close()
    values = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
    }
    task = (("outcome", "succeeded"), ("task", "testmind.test_execution.execute"))
    assert values[("testmind_tasks_total", task)] == 2
    sandbox = (("kind", "pytest"), ("result", "exit_1"))
    assert values[("testmind_sandbox_runs_total", sandbox)] == 2
    assert values[("testmind_sandbox_run_duration_seconds_sum", (("kind", "pytest"),))] == 4.0
