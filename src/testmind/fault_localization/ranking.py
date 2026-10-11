"""Ranking the functions that most likely hold the fault behind a set of failing tests.

The rule, fixed before it was measured (docs/fault-localization.md):

1. Production functions of the failure's traceback come first, innermost first: the exception
   was raised there, which is direct evidence.
2. Then every other function the failing tests executed, by Ochiai score, highest first.
   Functions no failing test executed are not suspects.
3. Tied scores get the worst rank of their tie, so a tie never looks better than it is.

The confidence says how much the ranking can be trusted (`Confidence`): a long tie at the top
is reported as low confidence, not hidden behind an arbitrary order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from testmind.fault_localization.formulas import ochiai
from testmind.fault_localization.models import Confidence, Localization, Suspect
from testmind.fault_localization.spectrum import Spectrum

DEFAULT_LIMIT = 10
MEDIUM_TIE = 3  # at most this many functions sharing the top score: medium confidence


def localize(
    spectrum: Spectrum,
    failing: Iterable[str],
    passing: Iterable[str],
    *,
    traceback: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> Localization:
    """`failing` and `passing` are test ids as TestMind reports them; `traceback` lists the
    'path::qualname' keys of the failure's production frames, innermost first."""
    index = {test: i for i, test in enumerate(spectrum.tests)}
    failed_ids = {index[t] for t in failing if t in index}
    passed_ids = {index[t] for t in passing if t in index} - failed_ids
    notes: list[str] = []
    if not failed_ids:
        notes.append("No failing test has per-test coverage, so there is nothing to rank")
        return Localization(
            suspects=[], confidence=Confidence.NONE, failing_tests=0,
            passing_tests=len(passed_ids), notes=notes,
        )  # fmt: skip

    scored: dict[str, tuple[float, int, int, int]] = {}  # key -> (score, failed, passed, line)
    for key, (line, tests) in spectrum.functions.items():
        failed = len(tests & failed_ids)
        if failed == 0:
            continue
        passed = len(tests & passed_ids)
        score = ochiai(failed, passed, len(failed_ids), len(passed_ids))
        scored[key] = (score, failed, passed, line)

    in_traceback = [key for key in dict.fromkeys(traceback) if key in scored]
    rest = sorted(
        (key for key in scored if key not in in_traceback),
        key=lambda key: (-scored[key][0], key),
    )
    suspects = [
        _suspect(key, scored[key], position, in_traceback=True)
        for position, key in enumerate(in_traceback, start=1)
    ]
    start = 0
    while start < len(rest) and len(suspects) < limit:
        end = start  # [start, end) is one tie: the same score
        while end < len(rest) and scored[rest[end]][0] == scored[rest[start]][0]:
            end += 1
        last_of_tie = len(in_traceback) + end
        suspects += [
            _suspect(key, scored[key], last_of_tie, in_traceback=False) for key in rest[start:end]
        ]
        start = end
    confidence = _confidence(suspects)
    return Localization(
        suspects=suspects[:limit],
        confidence=confidence,
        failing_tests=len(failed_ids),
        passing_tests=len(passed_ids),
        notes=notes,
    )


def _suspect(
    key: str, values: tuple[float, int, int, int], rank: int, *, in_traceback: bool
) -> Suspect:
    score, failed, passed, line = values
    path, _, qualname = key.partition("::")
    return Suspect(
        path=path, qualname=qualname, line=line, rank=rank, score=round(score, 4),
        failed=failed, passed=passed, in_traceback=in_traceback,
    )  # fmt: skip


def _confidence(suspects: list[Suspect]) -> Confidence:
    if not suspects:
        return Confidence.NONE
    top = suspects[0]
    if top.in_traceback or top.rank == 1:
        return Confidence.HIGH
    return Confidence.MEDIUM if top.rank <= MEDIUM_TIE else Confidence.LOW
