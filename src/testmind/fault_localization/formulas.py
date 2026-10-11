"""Suspiciousness formulas of spectrum-based fault localisation.

For one function: `failed` failing and `passed` passing tests executed it, out of
`total_failed` failing and `total_passed` passing tests. A function no failing test executed
cannot hold the fault they reveal, so every formula gives it 0.

- Ochiai (TestMind's choice, fixed before measuring: the standard reference formula of the
  literature): failed / sqrt(total_failed * (failed + passed)).
- DStar (*=2): failed² / (passed + total_failed - failed); infinite when every failing and
  no passing test executed it.
- Tarantula: (failed/total_failed) / (failed/total_failed + passed/total_passed).

DStar and Tarantula are reported for comparison only.
"""

from __future__ import annotations

import math
from collections.abc import Callable

Formula = Callable[[int, int, int, int], float]


def ochiai(failed: int, passed: int, total_failed: int, total_passed: int) -> float:
    if failed == 0 or total_failed == 0:
        return 0.0
    return failed / math.sqrt(total_failed * (failed + passed))


def dstar(failed: int, passed: int, total_failed: int, total_passed: int) -> float:
    if failed == 0:
        return 0.0
    denominator = passed + (total_failed - failed)
    return math.inf if denominator == 0 else failed**2 / denominator


def tarantula(failed: int, passed: int, total_failed: int, total_passed: int) -> float:
    if failed == 0 or total_failed == 0:
        return 0.0
    fail_ratio = failed / total_failed
    pass_ratio = passed / total_passed if total_passed else 0.0
    return fail_ratio / (fail_ratio + pass_ratio)


FORMULAS: dict[str, Formula] = {"ochiai": ochiai, "dstar": dstar, "tarantula": tarantula}
