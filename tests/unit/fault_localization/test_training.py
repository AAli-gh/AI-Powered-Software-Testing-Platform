"""Building training rows from labelled failures, and fitting the linear ranker on them."""

from __future__ import annotations

from testmind.fault_localization.learned import learned_localize
from testmind.fault_localization.spectrum import Spectrum
from testmind.fault_localization.training import example_rows, train


def _spectrum() -> Spectrum:
    return Spectrum(
        tests=("t0", "t1", "t2", "t3"),
        functions={
            "m.py::buggy": (10, frozenset({0, 1})),  # only failing tests
            "m.py::shared": (20, frozenset({0, 1, 2, 3})),  # everyone
            "m.py::clean": (30, frozenset({2, 3})),  # only passing — not a candidate
        },
    )


def test_example_rows_label_the_faulty_candidate() -> None:
    rows = example_rows(_spectrum(), ["t0", "t1"], ["t2", "t3"], "m.py::buggy")
    labels = [label for _vector, label in rows]
    assert sum(labels) == 1  # exactly the faulty function is positive
    assert len(rows) == 2  # buggy and shared were run by a failing test; clean was not


def test_example_rows_include_a_traceback_frame_coverage_missed() -> None:
    spectrum = Spectrum(tests=("t0", "t1"), functions={})  # coverage recorded nothing
    rows = example_rows(spectrum, ["t0", "t1"], [], "m.py::hidden", traceback=[("m.py::hidden", 5)])
    assert len(rows) == 1
    assert rows[0][1] == 1  # the hidden frame is a labelled candidate


def test_training_with_no_rows_returns_a_zero_model() -> None:
    model = train([])
    assert model.bias == 0.0
    assert set(model.weights) == {0.0}


def test_a_trained_model_ranks_the_faulty_function_first() -> None:
    # A tiny but separable dataset: the faulty function is the one only failing tests ran.
    spectrum = _spectrum()
    rows = example_rows(spectrum, ["t0", "t1"], ["t2", "t3"], "m.py::buggy")
    # Repeat the example so the fit has signal; all examples share the same shape here.
    model = train(rows * 20)
    localized = learned_localize(spectrum, ["t0", "t1"], ["t2", "t3"], model=model)
    assert localized.suspects[0].qualname == "buggy"
