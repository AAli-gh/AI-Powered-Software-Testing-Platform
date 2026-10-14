"""Turn labelled localisation examples into training rows, and fit the linear ranker.

An *example* is one failure whose faulty function is known (a bug injected by mutation, say):
its spectrum, its failing and passing test ids, the faulty function's key, and optionally the
traceback. `example_rows` turns it into one feature row per candidate function — exactly the
candidates the ranker considers — labelled 1 for the faulty function and 0 otherwise.

`train` fits a logistic-regression model on those rows with plain gradient descent (no external
library, deterministic: zero initialisation, a fixed number of steps). Features are standardised
during fitting for numerical stability, then folded back so the returned `LinearModel` scores
raw feature vectors. Pointwise classification is a simple, honest proxy for ranking; whether it
actually beats the deterministic rule is decided on held-out bugs, not assumed.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from testmind.fault_localization.features import FEATURE_NAMES, features
from testmind.fault_localization.model import LinearModel
from testmind.fault_localization.spectrum import Spectrum

Row = tuple[list[float], int]


def example_rows(
    spectrum: Spectrum,
    failing: Iterable[str],
    passing: Iterable[str],
    faulty_key: str,
    *,
    traceback: Sequence[tuple[str, int]] = (),
) -> list[Row]:
    """Feature rows for one labelled failure: one per candidate function, the faulty one = 1."""
    index = {test: i for i, test in enumerate(spectrum.tests)}
    failed_ids = {index[t] for t in failing if t in index}
    passed_ids = {index[t] for t in passing if t in index} - failed_ids
    total_failed = max(len(failed_ids), 1)
    total_passed = len(passed_ids)
    frames = {key for key, _ in traceback}

    rows: list[Row] = []
    seen: set[str] = set()
    for key, (_line, tests) in spectrum.functions.items():
        failed = len(tests & failed_ids)
        in_traceback = key in frames
        if failed == 0 and not in_traceback:
            continue
        passed = len(tests & passed_ids)
        seen.add(key)
        vector = features(failed, passed, total_failed, total_passed, in_traceback=in_traceback)
        rows.append((vector, 1 if key == faulty_key else 0))
    for key in frames - seen:  # a traceback frame coverage missed: counted as all-failing
        vector = features(total_failed, 0, total_failed, total_passed, in_traceback=True)
        rows.append((vector, 1 if key == faulty_key else 0))
    return rows


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp = math.exp(z)
    return exp / (1.0 + exp)


def train(
    rows: Sequence[Row], *, iterations: int = 800, learning_rate: float = 0.3, l2: float = 0.01
) -> LinearModel:
    """Fit a `LinearModel` by logistic regression. Deterministic: zero init, fixed steps."""
    width = len(FEATURE_NAMES)
    if not rows:
        return LinearModel(weights=tuple([0.0] * width), bias=0.0)

    columns = [[row[0][j] for row in rows] for j in range(width)]
    means = [sum(col) / len(col) for col in columns]
    stds = [
        math.sqrt(sum((x - m) ** 2 for x in col) / len(col)) or 1.0
        for col, m in zip(columns, means, strict=True)
    ]
    standardized = [[(row[0][j] - means[j]) / stds[j] for j in range(width)] for row in rows]
    labels = [float(row[1]) for row in rows]

    weights = [0.0] * width
    bias = 0.0
    n = len(rows)
    for _ in range(iterations):
        grad_w = [0.0] * width
        grad_b = 0.0
        for vector, label in zip(standardized, labels, strict=True):
            prediction = _sigmoid(bias + sum(w * x for w, x in zip(weights, vector, strict=True)))
            error = prediction - label
            grad_b += error
            for j in range(width):
                grad_w[j] += error * vector[j]
        bias -= learning_rate * grad_b / n
        for j in range(width):
            weights[j] -= learning_rate * (grad_w[j] / n + l2 * weights[j])

    # Fold the standardisation back so the model scores raw feature vectors.
    raw_weights = tuple(weights[j] / stds[j] for j in range(width))
    raw_bias = bias - sum(weights[j] * means[j] / stds[j] for j in range(width))
    return LinearModel(weights=raw_weights, bias=raw_bias)
