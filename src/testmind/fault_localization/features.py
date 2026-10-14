"""Features of one candidate function, for the learned fault-localisation ranker.

Every feature is computed from the same spectrum counts the deterministic rule uses — the
failing and passing tests that executed the function, out of the totals — plus whether the
function is a frame of the failure's traceback. Nothing here learns or trains: it turns those
counts into a fixed-order vector. The weights live in a model (model.py); training is separate.

Keeping this pure and fixed-order means a model trained on these vectors scores the same way in
production as in training, with no external service.
"""

from __future__ import annotations

import math

from testmind.fault_localization.formulas import dstar, ochiai, tarantula

FEATURE_NAMES: tuple[str, ...] = (
    "ochiai",
    "dstar_norm",  # DStar squashed to 0..1 (infinite → 1)
    "tarantula",
    "failed_ratio",  # share of failing tests that executed the function
    "passed_ratio",  # share of passing tests that executed it (lower is more suspicious)
    "only_failing",  # 1 when only failing tests executed it
    "support",  # share of all tests that executed it
    "in_traceback",  # 1 when the function is a frame of the failure's traceback
)


def features(
    failed: int, passed: int, total_failed: int, total_passed: int, *, in_traceback: bool
) -> list[float]:
    """The feature vector for a function, in `FEATURE_NAMES` order."""
    tf = max(total_failed, 0)
    tp = max(total_passed, 0)
    ds = dstar(failed, passed, tf, tp)
    dstar_norm = 1.0 if math.isinf(ds) else ds / (ds + 1.0)
    return [
        ochiai(failed, passed, tf, tp),
        dstar_norm,
        tarantula(failed, passed, tf, tp),
        failed / tf if tf else 0.0,
        passed / tp if tp else 0.0,
        1.0 if failed > 0 and passed == 0 else 0.0,
        (failed + passed) / (tf + tp) if (tf + tp) else 0.0,
        1.0 if in_traceback else 0.0,
    ]
