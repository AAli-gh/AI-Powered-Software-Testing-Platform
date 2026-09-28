import importlib
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery import Celery, Task

from testmind.core.config import Settings, get_settings
from testmind.workers.celery_app import DEFAULT_QUEUE, create_celery_app
from testmind.workers.tasks import EXECUTE_ANALYSIS_RUN


def settings_for(root: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@db/x",  # type: ignore[arg-type]
        redis_url="redis://redis:6379/0",  # type: ignore[arg-type]
        repos_root=root,
        celery_task_time_limit_seconds=600,
    )


def bound_settings(task: Task) -> Settings:  # type: ignore[type-arg]
    closure = task.run.__closure__ or ()
    return next(cell.cell_contents for cell in closure if isinstance(cell.cell_contents, Settings))


def test_task_registered_with_public_name(tmp_path: Path) -> None:
    assert EXECUTE_ANALYSIS_RUN in create_celery_app(settings_for(tmp_path)).tasks


def test_each_app_runs_with_its_own_settings(tmp_path: Path) -> None:
    """Regression: Celery's default `shared=True` leaked the first app's task into later apps."""
    first = create_celery_app(settings_for(tmp_path / "first"))
    second = create_celery_app(settings_for(tmp_path / "second"))

    assert bound_settings(first.tasks[EXECUTE_ANALYSIS_RUN]).repos_root.name == "first"
    assert bound_settings(second.tasks[EXECUTE_ANALYSIS_RUN]).repos_root.name == "second"


def test_safe_queue_configuration(tmp_path: Path) -> None:
    conf = create_celery_app(settings_for(tmp_path)).conf
    assert conf.accept_content == ["json"]
    assert conf.task_serializer == "json"
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_default_queue == DEFAULT_QUEUE
    assert conf.task_time_limit == 600
    assert conf.task_soft_time_limit < conf.task_time_limit
    # Redis must not re-deliver a message while its task may still legitimately be running.
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit


@pytest.fixture
def worker_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTMIND_DATABASE_URL", "postgresql+asyncpg://u:p@db/x")
    monkeypatch.setenv("TESTMIND_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("TESTMIND_REPOS_ROOT", str(tmp_path))
    monkeypatch.setenv("TESTMIND_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    sys.modules.pop("testmind.workers.main", None)
    get_settings.cache_clear()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures("worker_env")
def test_worker_entrypoint_builds_app_from_environment() -> None:
    main = importlib.import_module("testmind.workers.main")

    assert isinstance(main.app, Celery)
    assert EXECUTE_ANALYSIS_RUN in main.app.tasks

    main._configure_worker_logging()
    assert logging.getLogger().level == logging.WARNING


@pytest.mark.usefixtures("worker_env")
def test_worker_serves_metrics_only_when_a_port_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from testmind.workers import metrics  # noqa: PLC0415

    served: list[int] = []
    monkeypatch.setattr(metrics, "serve", served.append)
    main = importlib.import_module("testmind.workers.main")
    main._serve_metrics()
    assert served == []

    sys.modules.pop("testmind.workers.main", None)
    monkeypatch.setenv("TESTMIND_WORKER_METRICS_PORT", "9100")
    get_settings.cache_clear()
    main = importlib.import_module("testmind.workers.main")
    main._serve_metrics()
    assert served == [9100]
