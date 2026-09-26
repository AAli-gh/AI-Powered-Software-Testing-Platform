import json

import pytest

from testmind.failure_engine import FailureCaptureError, parse_failures_json
from testmind.failure_engine.capture import parse_collection_error
from tests.unit.failure_engine.test_analyzer import CAPTURE


def test_real_capture_parses() -> None:
    capture = parse_failures_json(CAPTURE.read_text())
    assert (capture.version, capture.rootdir, capture.dropped) == (1, "/workspace/src", 0)
    assert len(capture.failures) == 14


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("not json", "Not a TestMind failure capture"),
        (json.dumps({"version": 1}), "Not a TestMind failure capture"),
        (
            json.dumps({"version": 1, "rootdir": "/w", "failures": [{"name": "x"}]}),
            "Not a TestMind failure capture",
        ),
        (
            json.dumps(
                {
                    "version": 1,
                    "rootdir": "/w",
                    "failures": [{"phase": "call", "name": "x" * 7000}],
                }
            ),
            "Not a TestMind failure capture",  # strings are bounded
        ),
        (json.dumps({"version": 2, "rootdir": "/w", "failures": []}), "Unsupported"),
    ],
)
def test_invalid_captures_are_rejected(text: str, error: str) -> None:
    with pytest.raises(FailureCaptureError, match=error):
        parse_failures_json(text)


def test_import_error_during_collection() -> None:
    longrepr = parse_failures_json(CAPTURE.read_text()).failures[0].longrepr or ""
    parsed = parse_collection_error(longrepr)
    assert (parsed.exception_type, parsed.message) == (
        "ModuleNotFoundError",
        "No module named 'not_installed_package'",
    )
    # The innermost `path:line: in` location, not the importlib frame before it.
    assert (parsed.path, parsed.line) == ("tests/test_missing.py", 1)


def test_syntax_error_names_the_offending_file() -> None:
    longrepr = parse_failures_json(CAPTURE.read_text()).failures[1].longrepr or ""
    parsed = parse_collection_error(longrepr)
    assert (parsed.exception_type, parsed.message) == ("SyntaxError", "invalid syntax")
    assert (parsed.path, parsed.line) == ("/workspace/src/tests/test_syntax.py", 1)


def test_unstructured_collection_error() -> None:
    parsed = parse_collection_error("something went wrong\n")
    assert (parsed.exception_type, parsed.message, parsed.path, parsed.line) == (
        None,
        "something went wrong",
        None,
        None,
    )
