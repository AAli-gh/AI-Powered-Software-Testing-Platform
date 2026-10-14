"""The learned ranker's model: a small linear scorer over the feature vector.

A linear model is deliberately simple — weights and a bias, scored with a dot product — so the
ranking stays explainable, needs no runtime dependency, and ships as a few numbers in a JSON
file. It orders the suspect functions; it does not decide anything on its own, and the
deterministic rule remains the default until a trained model is shown to do better on held-out
bugs (docs/evaluation.md).

`DEFAULT_MODEL` is hand-set to reproduce the deterministic rule's priority (Ochiai first, a
function only failing tests ran next), so turning the learned ranker on with no trained model
changes nothing. Training replaces these weights with learned ones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from testmind.fault_localization.features import FEATURE_NAMES


class ModelError(ValueError):
    """A stored model does not match the current feature set."""


@dataclass(frozen=True, slots=True)
class LinearModel:
    weights: tuple[float, ...]
    bias: float
    feature_names: tuple[str, ...] = FEATURE_NAMES

    def __post_init__(self) -> None:
        if len(self.weights) != len(self.feature_names):
            raise ModelError(f"{len(self.weights)} weights for {len(self.feature_names)} features")
        if tuple(self.feature_names) != FEATURE_NAMES:
            raise ModelError("the model's feature names do not match the current feature set")

    def score(self, vector: list[float]) -> float:
        return self.bias + sum(w * x for w, x in zip(self.weights, vector, strict=True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": list(self.weights),
            "bias": self.bias,
            "feature_names": list(self.feature_names),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinearModel:
        try:
            return cls(
                weights=tuple(float(w) for w in data["weights"]),
                bias=float(data["bias"]),
                feature_names=tuple(data.get("feature_names", FEATURE_NAMES)),
            )
        except (KeyError, TypeError) as exc:
            raise ModelError(f"malformed model: {exc}") from exc

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", "utf-8")

    @classmethod
    def load(cls, path: Path) -> LinearModel:
        return cls.from_dict(json.loads(path.read_text("utf-8")))


# Order matches FEATURE_NAMES. Ochiai dominates, a function only failing tests ran gets a
# nudge, more passing tests make a function less suspicious — the deterministic rule's priority.
DEFAULT_MODEL = LinearModel(
    weights=(3.0, 1.0, 1.0, 0.5, -1.0, 0.5, 0.0, 0.0),
    bias=0.0,
)
