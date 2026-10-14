"""Compare the deterministic Ochiai ranking with the learned ranker on injected-bug examples.

The comparison is honest about generalisation: the model is evaluated with a leave-one-project-out
split, so it is always scored on a project whose bugs it never trained on. For each example the
metric is the rank of the known faulty function among the suspects (ties take the worst rank, as
the ranker reports them); we summarise top-1, top-5 and mean reciprocal rank.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from testmind.fault_localization.learned import model_scorer
from testmind.fault_localization.models import Localization
from testmind.fault_localization.ranking import Scorer, localize
from testmind.fault_localization.spectrum import Spectrum
from testmind.fault_localization.training import Row, example_rows, train

TOP5 = 5
Localizer = Callable[[Spectrum, list[str], list[str]], Localization]


@dataclass(frozen=True, slots=True)
class Example:
    project: str
    faulty_key: str
    spectrum: Spectrum
    failing: list[str]
    passing: list[str]

    def rows(self) -> list[Row]:
        return example_rows(self.spectrum, self.failing, self.passing, self.faulty_key)


def load(path: Path) -> list[Example]:
    examples: list[Example] = []
    for line in path.read_text("utf-8").splitlines():
        raw = json.loads(line)
        functions = {
            key: (line_no, frozenset(idx)) for key, (line_no, idx) in raw["functions"].items()
        }
        examples.append(
            Example(
                project=raw["project"],
                faulty_key=raw["faulty_key"],
                spectrum=Spectrum(tests=tuple(raw["tests"]), functions=functions),
                failing=list(raw["failing"]),
                passing=list(raw["passing"]),
            )
        )
    return examples


def _rank(localized: Localization, faulty_key: str) -> int | None:
    for suspect in localized.suspects:
        if f"{suspect.path}::{suspect.qualname}" == faulty_key:
            return suspect.rank
    return None


@dataclass(frozen=True, slots=True)
class Metrics:
    examples: int
    top1: int
    top5: int
    mrr: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "examples": self.examples,
            "top1": self.top1,
            "top5": self.top5,
            "mrr": round(self.mrr, 4),
        }


def score(examples: Sequence[Example], localizer: Localizer) -> Metrics:
    top1 = top5 = 0
    reciprocal = 0.0
    for ex in examples:
        rank = _rank(localizer(ex.spectrum, ex.failing, ex.passing), ex.faulty_key)
        if rank is None:
            continue
        top1 += rank == 1
        top5 += rank <= TOP5
        reciprocal += 1.0 / rank
    return Metrics(len(examples), top1, top5, reciprocal / len(examples) if examples else 0.0)


def _rule(spectrum: Spectrum, failing: list[str], passing: list[str]) -> Localization:
    return localize(spectrum, failing, passing)


def cross_validate(examples: Sequence[Example]) -> dict[str, object]:
    """Leave-one-project-out: train on the other projects, score the held-out one."""
    projects = sorted({ex.project for ex in examples})
    held_rule = []
    held_learned = []
    for project in projects:
        train_rows: list[Row] = []
        for ex in examples:
            if ex.project != project:
                train_rows.extend(ex.rows())
        scorer = model_scorer(train(train_rows))

        def learned(
            s: Spectrum, f: list[str], p: list[str], scorer: Scorer = scorer
        ) -> Localization:
            return localize(s, f, p, scorer=scorer)

        test = [ex for ex in examples if ex.project == project]
        held_rule.append(score(test, _rule))
        held_learned.append(score(test, learned))
    return {
        "projects": projects,
        "rule": _aggregate(held_rule).as_dict(),
        "learned": _aggregate(held_learned).as_dict(),
        "per_project": [
            {"project": project, "rule": rule.as_dict(), "learned": learned_m.as_dict()}
            for project, rule, learned_m in zip(projects, held_rule, held_learned, strict=True)
        ],
    }


def _aggregate(parts: Sequence[Metrics]) -> Metrics:
    examples = sum(m.examples for m in parts)
    return Metrics(
        examples=examples,
        top1=sum(m.top1 for m in parts),
        top5=sum(m.top5 for m in parts),
        mrr=sum(m.mrr * m.examples for m in parts) / examples if examples else 0.0,
    )
