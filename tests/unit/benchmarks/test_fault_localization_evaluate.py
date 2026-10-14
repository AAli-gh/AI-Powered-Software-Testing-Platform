"""The fault-localisation benchmark's pure comparison logic.

These exercise `load`, `score`, `_rule` and `cross_validate` on synthetic spectra built in
memory, so they run in CI without a subprocess, pytest-in-pytest, or coverage contexts (the
dataset generator is tested by actually running it, separately). The point is to pin the
metric arithmetic and the leave-one-project-out split, not to re-prove the ranker.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from benchmarks.fault_localization.evaluate import (
    Example,
    _rule,
    cross_validate,
    load,
    score,
)
from testmind.fault_localization.spectrum import Spectrum


def _spectrum(functions: dict[str, tuple[int, list[int]]], tests: list[str]) -> Spectrum:
    return Spectrum(
        tests=tuple(tests),
        functions={key: (line, frozenset(idx)) for key, (line, idx) in functions.items()},
    )


def _clean(project: str, faulty: str) -> Example:
    """A tiny example where the fault is obvious: only `m.py::bad` is run by the one failing
    test, so every scorer ranks it first."""
    return Example(
        project=project,
        faulty_key=faulty,
        spectrum=_spectrum(
            {"m.py::bad": (1, [0]), "m.py::good": (5, [1])},
            ["m.py::test_bad", "m.py::test_good"],
        ),
        failing=["m.py::test_bad"],
        passing=["m.py::test_good"],
    )


def test_rule_ranks_the_only_failing_function_first() -> None:
    example = _clean("p", "m.py::bad")
    localized = _rule(example.spectrum, example.failing, example.passing)
    top = localized.suspects[0]
    assert f"{top.path}::{top.qualname}" == "m.py::bad"
    assert top.rank == 1


def test_score_is_perfect_when_the_fault_is_always_top() -> None:
    examples = [_clean(f"p{i}", "m.py::bad") for i in range(3)]
    metrics = score(examples, _rule)
    assert metrics.examples == 3
    assert metrics.top1 == 3
    assert metrics.top5 == 3
    assert metrics.mrr == 1.0


def test_score_counts_a_missed_fault_as_rank_absent() -> None:
    # The faulty key names a function the spectrum does not contain, so `_rank` returns None and
    # the example contributes to the denominator but not to top-1/top-5/MRR.
    example = _clean("p", "m.py::missing")
    metrics = score([example], _rule)
    assert metrics.examples == 1
    assert metrics.top1 == 0
    assert metrics.mrr == 0.0


def test_mrr_uses_the_reciprocal_rank() -> None:
    # Two functions are run only by the failing test, so they tie; ties take the worst rank (2),
    # and here the faulty one lands at rank 2 -> reciprocal 0.5.
    example = Example(
        project="p",
        faulty_key="m.py::b",
        spectrum=_spectrum(
            {"m.py::a": (1, [0]), "m.py::b": (5, [0]), "m.py::c": (9, [1])},
            ["m.py::test_fail", "m.py::test_ok"],
        ),
        failing=["m.py::test_fail"],
        passing=["m.py::test_ok"],
    )
    metrics = score([example], _rule)
    assert metrics.top1 == 0
    assert metrics.top5 == 1
    assert metrics.mrr == 0.5


def test_load_round_trips_the_jsonl_form(tmp_path: Path) -> None:
    example = _clean("p", "m.py::bad")
    line = {
        "project": example.project,
        "faulty_key": example.faulty_key,
        "tests": list(example.spectrum.tests),
        "functions": {
            key: [line_no, sorted(idx)]
            for key, (line_no, idx) in example.spectrum.functions.items()
        },
        "failing": example.failing,
        "passing": example.passing,
    }
    path = tmp_path / "dataset.jsonl"
    path.write_text(json.dumps(line) + "\n", "utf-8")

    loaded = load(path)
    assert len(loaded) == 1
    assert loaded[0] == example


def test_cross_validate_splits_by_project_and_reports_both_rankers() -> None:
    examples = [_clean("calc", "m.py::bad"), _clean("pricing", "m.py::bad")]
    summary = cross_validate(examples)
    assert summary["projects"] == ["calc", "pricing"]
    assert cast("dict[str, int]", summary["rule"])["top1"] == 2
    assert cast("dict[str, int]", summary["learned"])["examples"] == 2
    per_project = cast("list[dict[str, object]]", summary["per_project"])
    assert [p["project"] for p in per_project] == ["calc", "pricing"]


def test_cross_validate_is_empty_without_examples() -> None:
    summary = cross_validate([])
    assert summary["projects"] == []
    assert cast("dict[str, int]", summary["rule"])["examples"] == 0
    assert cast("dict[str, float]", summary["rule"])["mrr"] == 0.0
