"""pytest plugin baked into the TestMind sandbox image (it is not part of the testmind package).

It records structured facts about every failure, straight from pytest instead of from
formatted traceback text:
- the phase (setup, call, teardown or collection);
- the exception type, its message and its cause or context chain;
- the traceback frames (file, line, function, source line);
- a few local variables of the innermost frame in the repository or the tests.

The result is written to /out/failures.json. TestMind reads it back as untrusted input
(docs/failure-analysis.md).
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

OUTPUT = os.environ.get("TESTMIND_FAILURES_FILE", "/out/failures.json")
WORKSPACE = "/workspace/"
MAX_FAILURES = 500
MAX_FRAMES = 30
MAX_MESSAGE = 2000
MAX_CODE = 300
MAX_LOCALS = 12
MAX_REPR = 200
MAX_CHAIN = 3
MAX_LONGREPR = 6000

_records: list[dict[str, Any]] = []
_counts = {"dropped": 0}


def _add(record: dict[str, Any]) -> None:
    if len(_records) < MAX_FAILURES:
        _records.append(record)
    else:
        _counts["dropped"] += 1


def _repr(value: object) -> str:
    try:
        text = repr(value)
    except Exception as exc:  # a broken __repr__ must not break the run
        text = f"<repr failed: {type(exc).__name__}>"
    return text if len(text) <= MAX_REPR else text[:MAX_REPR] + "..."


def _type_name(exc: BaseException) -> str:
    kind = type(exc)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def _message(exc: BaseException) -> str:
    try:
        return str(exc)[:MAX_MESSAGE]
    except Exception:
        return "<str failed>"


def _chain(exc: BaseException) -> list[dict[str, str]]:
    chain = []
    current = exc
    while len(chain) < MAX_CHAIN:
        cause = current.__cause__
        if cause is None and not current.__suppress_context__:
            cause = current.__context__
        if cause is None:
            break
        chain.append({"type": _type_name(cause), "message": _message(cause)})
        current = cause
    return chain


def _frames(excinfo: pytest.ExceptionInfo[BaseException]) -> tuple[list[dict[str, Any]], Any]:
    entries = list(excinfo.traceback)
    # pytest's and pluggy's own frames come before the first frame of the code under test.
    start = next((i for i, e in enumerate(entries) if str(e.path).startswith(WORKSPACE)), 0)
    entries = entries[start:][-MAX_FRAMES:]
    frames = []
    for entry in entries:
        try:
            code = str(entry.statement).strip()[:MAX_CODE]
        except Exception:  # source may be unavailable
            code = ""
        frames.append(
            {
                "path": str(entry.path),
                "line": entry.lineno + 1,
                "function": entry.name,
                "code": code,
            }
        )
    innermost = next((e for e in reversed(entries) if str(e.path).startswith(WORKSPACE)), None)
    return frames, innermost


def _locals(entry: Any) -> dict[str, str]:
    if entry is None:
        return {}
    try:
        names = list(entry.locals.items())
    except Exception:
        return {}
    # "@py_assert…" are pytest's assertion-rewriting temporaries.
    visible = [(k, v) for k, v in names if not k.startswith(("@", "__"))]
    return {name: _repr(value) for name, value in visible[:MAX_LOCALS]}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Any:
    outcome = yield
    report = outcome.get_result()
    if not report.failed:  # passed, skipped and expected failures (xfail)
        return
    record: dict[str, Any] = {
        "phase": call.when,
        "path": str(item.path),
        "name": item.name,
        "line": item.location[1] + 1 if item.location[1] is not None else None,
    }
    if call.excinfo is None:  # e.g. xfail(strict=True) that passed
        record["unexpected_pass"] = True
        record["longrepr"] = str(report.longrepr)[:MAX_LONGREPR]
    else:
        exc = call.excinfo.value
        frames, innermost = _frames(call.excinfo)
        record.update(
            exception={"type": _type_name(exc), "message": _message(exc)},
            chain=_chain(exc),
            frames=frames,
            locals=_locals(innermost),
        )
    _add(record)


def pytest_collectreport(report: pytest.CollectReport) -> None:
    if report.failed:
        # Import and syntax errors reach pytest as formatted text; TestMind parses the last
        # "E   Type: message" line of it.
        _add(
            {
                "phase": "collect",
                "nodeid": report.nodeid,
                "longrepr": str(report.longrepr)[:MAX_LONGREPR],
            }
        )


def pytest_sessionfinish(session: pytest.Session) -> None:
    document = {
        "version": 1,
        "rootdir": str(session.config.rootpath),
        "failures": _records,
        "dropped": _counts["dropped"],
    }
    # /out is missing when pytest is not run by TestMind: then there is nothing to report.
    with contextlib.suppress(OSError):
        Path(OUTPUT).write_text(json.dumps(document), encoding="utf-8")
