import json
import logging

import pytest

from testmind.core.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_root_logger() -> object:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_structlog_and_stdlib_share_json_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)

    get_logger("testmind.test").info("structlog_event", run_id="abc")
    logging.getLogger("third.party").warning("stdlib message")

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["event"] == "structlog_event"
    assert lines[0]["run_id"] == "abc"
    assert lines[0]["level"] == "info"
    assert "timestamp" in lines[0]
    assert lines[1]["event"] == "stdlib message"
    assert lines[1]["level"] == "warning"
    assert lines[1]["logger"] == "third.party"


def test_level_filters_records(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING", json=True)
    logging.getLogger("x").info("hidden")
    assert capsys.readouterr().out == ""


def test_console_renderer(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=False)
    logging.getLogger("x").info("human readable")
    assert "human readable" in capsys.readouterr().out
