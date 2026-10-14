"""The learned fault-localisation ranker: features, the linear model, and tail re-ordering."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from testmind.fault_localization.features import FEATURE_NAMES, features
from testmind.fault_localization.learned import learned_localize, model_scorer
from testmind.fault_localization.model import DEFAULT_MODEL, LinearModel, ModelError
from testmind.fault_localization.ranking import localize
from testmind.fault_localization.spectrum import Spectrum


def test_feature_vector_matches_the_names_and_is_bounded() -> None:
    vector = features(2, 0, 2, 3, in_traceback=True)
    assert len(vector) == len(FEATURE_NAMES)
    assert vector[FEATURE_NAMES.index("only_failing")] == 1.0
    assert vector[FEATURE_NAMES.index("in_traceback")] == 1.0
    assert 0.0 <= vector[FEATURE_NAMES.index("ochiai")] <= 1.0
    assert 0.0 <= vector[FEATURE_NAMES.index("dstar_norm")] <= 1.0


def test_dstar_norm_is_one_when_only_failing_tests_ran_it() -> None:
    # DStar is infinite when every failing and no passing test executed the function.
    vector = features(3, 0, 3, 5, in_traceback=False)
    assert vector[FEATURE_NAMES.index("dstar_norm")] == 1.0
    assert not math.isinf(vector[FEATURE_NAMES.index("dstar_norm")])


def test_features_tolerate_empty_totals() -> None:
    assert features(0, 0, 0, 0, in_traceback=False) == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_model_score_is_the_dot_product_plus_bias() -> None:
    model = LinearModel(weights=tuple([1.0] + [0.0] * (len(FEATURE_NAMES) - 1)), bias=0.5)
    assert model.score([2.0] + [9.0] * (len(FEATURE_NAMES) - 1)) == pytest.approx(2.5)


def test_model_rejects_a_wrong_width_or_renamed_features() -> None:
    with pytest.raises(ModelError):
        LinearModel(weights=(1.0, 2.0), bias=0.0)
    with pytest.raises(ModelError):
        # right width, but the names are not the current feature set
        LinearModel(weights=(0.0,), bias=0.0, feature_names=("x",))


def test_model_round_trips_through_json(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    DEFAULT_MODEL.save(path)
    loaded = LinearModel.load(path)
    assert loaded == DEFAULT_MODEL


def test_malformed_model_dict_is_rejected() -> None:
    with pytest.raises(ModelError):
        LinearModel.from_dict({"bias": 0.0})  # no weights


def _spectrum() -> Spectrum:
    # tests 0,1 fail; 2,3 pass. `buggy` is run only by failing tests; `shared` by everyone.
    return Spectrum(
        tests=("t0", "t1", "t2", "t3"),
        functions={
            "m.py::buggy": (10, frozenset({0, 1})),
            "m.py::shared": (20, frozenset({0, 1, 2, 3})),
            "m.py::clean": (30, frozenset({2, 3})),
        },
    )


def test_default_model_reproduces_the_deterministic_order() -> None:
    spectrum = _spectrum()
    failing, passing = ["t0", "t1"], ["t2", "t3"]
    rule = localize(spectrum, failing, passing)
    learned = learned_localize(spectrum, failing, passing)
    assert [s.qualname for s in learned.suspects] == [s.qualname for s in rule.suspects]
    assert learned.suspects[0].qualname == "buggy"  # only failing tests ran it: most suspicious


def test_a_model_can_reorder_the_tail() -> None:
    spectrum = _spectrum()
    # A model keyed entirely on passed_ratio (negative) would still favour `buggy`; instead use
    # one that rewards `support` so the everyone-runs `shared` outranks `buggy`.
    names = list(FEATURE_NAMES)
    weights = [0.0] * len(names)
    weights[names.index("support")] = 1.0
    model = LinearModel(weights=tuple(weights), bias=0.0)
    learned = learned_localize(spectrum, ["t0", "t1"], ["t2", "t3"], model=model)
    assert learned.suspects[0].qualname == "shared"  # highest support now ranks first


def test_model_scorer_is_a_plain_callable() -> None:
    scorer = model_scorer(DEFAULT_MODEL)
    assert scorer(2, 0, 2, 3) > scorer(1, 3, 2, 3)  # only-failing beats mostly-passing
