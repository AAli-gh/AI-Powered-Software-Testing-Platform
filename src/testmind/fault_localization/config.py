"""Choose the fault-localisation scorer from configuration.

The deterministic Ochiai rule is the default and is what the evaluation measures
(docs/fault-localization.md). The learned ranker is a non-default alternative: it reorders only
the non-traceback suspects and, on the corpus measured so far, matches the rule without beating
it, so it is opt-in. This resolves the setting once into a `ranking.Scorer`, so neither the
runner nor the services need to know how a ranker is built.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from testmind.fault_localization.formulas import ochiai
from testmind.fault_localization.learned import model_scorer
from testmind.fault_localization.model import DEFAULT_MODEL, LinearModel
from testmind.fault_localization.ranking import Scorer

Ranker = Literal["ochiai", "learned"]


def select_scorer(ranker: Ranker, model_path: Path | None = None) -> Scorer:
    """The scorer `ranking.localize` should use.

    `"ochiai"` (the default) returns the deterministic formula. `"learned"` returns a model
    scorer: the model at `model_path` when one is configured, otherwise the built-in
    `DEFAULT_MODEL` (which reproduces the rule's priority). A configured path that cannot be
    read raises, so a misconfiguration fails loudly rather than silently falling back.
    """
    if ranker == "ochiai":
        return ochiai
    model = LinearModel.load(model_path) if model_path is not None else DEFAULT_MODEL
    return model_scorer(model)
