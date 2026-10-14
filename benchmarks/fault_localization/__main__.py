"""Generate injected-bug examples, train the learned ranker, and compare it with the rule.

uv run python -m benchmarks.fault_localization generate   # corpus -> dataset.jsonl
uv run python -m benchmarks.fault_localization evaluate   # leave-one-project-out compare
uv run python -m benchmarks.fault_localization train      # fit on all, write model.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from benchmarks.fault_localization.evaluate import cross_validate, load
from benchmarks.fault_localization.generate import generate, write_jsonl
from testmind.fault_localization.training import train

HERE = Path(__file__).resolve().parent
DATASET = HERE / "results" / "dataset.jsonl"
RESULTS = HERE / "results" / "comparison.json"
MODEL = HERE / "results" / "model.json"


def _generate() -> None:
    stats = generate()
    write_jsonl(stats, DATASET)
    print(f"mutants tried: {stats.mutants}; killed (examples): {stats.killed}")  # noqa: T201
    print(f"wrote {len(stats.examples)} examples to {DATASET}")  # noqa: T201


def _evaluate() -> None:
    examples = load(DATASET)
    summary = cross_validate(examples)
    RESULTS.write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    rule = summary["rule"]
    learned = summary["learned"]
    print(f"examples: {rule['examples']} across projects {summary['projects']}")  # type: ignore[index]  # noqa: T201
    print(f"rule    : top1={rule['top1']} top5={rule['top5']} mrr={rule['mrr']}")  # type: ignore[index]  # noqa: T201
    print(f"learned : top1={learned['top1']} top5={learned['top5']} mrr={learned['mrr']}")  # type: ignore[index]  # noqa: T201
    print(f"wrote {RESULTS}")  # noqa: T201


def _train() -> None:
    examples = load(DATASET)
    rows = [row for ex in examples for row in ex.rows()]
    model = train(rows)
    model.save(MODEL)
    print(f"trained on {len(rows)} rows from {len(examples)} examples; wrote {MODEL}")  # noqa: T201


def main(argv: list[str]) -> int:
    commands = {"generate": _generate, "evaluate": _evaluate, "train": _train}
    if len(argv) != 1 or argv[0] not in commands:
        print(__doc__)  # noqa: T201
        return 2
    commands[argv[0]]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
