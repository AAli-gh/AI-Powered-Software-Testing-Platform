"""The learned fault-localisation ranker: order the non-traceback suspects with a model.

It reuses the deterministic rule wholesale (`ranking.localize`) and changes only how the
non-traceback suspects are ordered — by a model's score over their features instead of raw
Ochiai. Traceback-first, ties getting the worst rank, and the confidence are unchanged, so the
learned ranker can only reorder the tail, never weaken the strong traceback evidence.

The deterministic rule stays the default; this is used only when a scan opts into the learned
ranker and a model is configured.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from testmind.fault_localization.features import features
from testmind.fault_localization.model import DEFAULT_MODEL, LinearModel
from testmind.fault_localization.models import Localization
from testmind.fault_localization.ranking import DEFAULT_LIMIT, Scorer, localize
from testmind.fault_localization.spectrum import Spectrum


def model_scorer(model: LinearModel) -> Scorer:
    """A `ranking.Scorer` that scores a tail function with the model over its features."""

    def score(failed: int, passed: int, total_failed: int, total_passed: int) -> float:
        vector = features(failed, passed, total_failed, total_passed, in_traceback=False)
        return model.score(vector)

    return score


def learned_localize(
    spectrum: Spectrum,
    failing: Iterable[str],
    passing: Iterable[str],
    *,
    traceback: Sequence[tuple[str, int]] = (),
    limit: int = DEFAULT_LIMIT,
    model: LinearModel = DEFAULT_MODEL,
) -> Localization:
    return localize(
        spectrum, failing, passing, traceback=traceback, limit=limit, scorer=model_scorer(model)
    )
