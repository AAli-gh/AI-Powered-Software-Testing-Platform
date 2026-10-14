"""Selecting the fault-localisation scorer from configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from testmind.fault_localization.config import select_scorer
from testmind.fault_localization.formulas import ochiai
from testmind.fault_localization.learned import model_scorer
from testmind.fault_localization.model import DEFAULT_MODEL


def test_ochiai_is_the_deterministic_formula() -> None:
    # The default ranker returns the Ochiai function itself, not a wrapper, so suite runs keep
    # scoring exactly as the evaluation measured.
    assert select_scorer("ochiai") is ochiai


def test_learned_without_a_path_uses_the_default_model() -> None:
    scorer = select_scorer("learned")
    expected = model_scorer(DEFAULT_MODEL)
    for args in [(1, 0, 1, 1), (2, 1, 3, 5), (0, 3, 2, 4)]:
        assert scorer(*args) == pytest.approx(expected(*args))


def test_learned_loads_a_configured_model(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    DEFAULT_MODEL.save(path)
    scorer = select_scorer("learned", path)
    # Loaded model scores identically to the in-memory default it was saved from.
    assert scorer(2, 1, 3, 5) == pytest.approx(select_scorer("learned")(2, 1, 3, 5))


def test_a_missing_model_path_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        select_scorer("learned", tmp_path / "absent.json")
