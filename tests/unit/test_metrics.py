"""Sandbox results are folded into a fixed set of label values."""

from __future__ import annotations

import pytest

from testmind.core.metrics import sandbox_result


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "expected"),
    [
        (0, False, "exit_0"),
        (1, False, "exit_1"),
        (5, False, "exit_5"),
        (137, False, "exit_137"),
        (-1, True, "timeout"),
        (0, True, "timeout"),  # a timeout wins over whatever code was left
        (42, False, "other"),  # an arbitrary code never becomes its own series
        (None, False, "other"),
    ],
)
def test_sandbox_result(exit_code: int | None, timed_out: bool, expected: str) -> None:
    assert sandbox_result(exit_code, timed_out=timed_out) == expected
