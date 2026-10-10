"""Run TestMind on BugsInPy bugs, or report the results (docs/evaluation.md).

    # TestMind must be running with its repositories root at WORKSPACE (make up-sandbox with
    # TESTMIND_HOST_REPOS_DIR=WORKSPACE); BugsInPy is a clone of github.com/soarsmu/BugsInPy.
    uv run python -m benchmarks.bugsinpy run --dataset BugsInPy --workspace WORKSPACE \\
        --cache CACHE --projects thefuck,tqdm --results results.jsonl
    uv run python -m benchmarks.bugsinpy report --results results.jsonl

`run` appends one JSON line per bug and skips bugs already in the file, so it can be resumed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from benchmarks.bugsinpy.client import TestMind
from benchmarks.bugsinpy.dataset import bug_numbers, load_bug, to_node_id
from benchmarks.bugsinpy.ground_truth import ground_truth
from benchmarks.bugsinpy.prepare import PreparationError, prepare
from benchmarks.bugsinpy.score import BugResult, score, summarise


def evaluate(args: argparse.Namespace, testmind: TestMind, project: str, number: int) -> BugResult:
    bug = load_bug(args.dataset / "projects", project, number)
    try:
        buggy, fixed = prepare(bug, args.cache, args.workspace)
    except (PreparationError, OSError) as exc:
        return BugResult(bug.name, "preparation_error", detail=str(exc)[-500:])
    try:
        targets = [to_node_id(t, buggy) for t in bug.targets]
        if not targets or None in targets:
            return BugResult(bug.name, "target_not_found", detail=", ".join(bug.targets))
        sources = {}
        for path in ground_truth(bug.patch, {}).files:
            file = buggy / path
            if file.is_file():
                sources[path] = file.read_text("utf-8", errors="replace")
        truth = ground_truth(bug.patch, sources)
        before = testmind.run_suite(buggy.name)
        after = testmind.run_suite(fixed.name)
        return score(bug.name, [t for t in targets if t], truth, before, after)
    finally:
        if args.clean:
            shutil.rmtree(buggy, ignore_errors=True)
            shutil.rmtree(fixed, ignore_errors=True)


def run(args: argparse.Namespace) -> int:
    done = set()
    if args.results.exists():
        done = {json.loads(line)["bug"] for line in args.results.read_text().splitlines() if line}
    testmind = TestMind(args.url, os.environ.get("TESTMIND_API_TOKEN") or None)
    for project in args.projects.split(","):
        numbers = bug_numbers(args.dataset / "projects", project)[: args.max_per_project]
        for number in numbers:
            if f"{project}-{number}" in done:
                continue
            result = evaluate(args, testmind, project, number)
            with args.results.open("a") as out:
                out.write(json.dumps(result.to_json()) + "\n")
            print(f"{result.bug}: {result.status} {result.blamed or ''} {result.detail}"[:200],  # noqa: T201
                  flush=True)  # fmt: skip
    return 0


def report(args: argparse.Namespace) -> int:
    rows = [json.loads(line) for line in args.results.read_text().splitlines() if line]
    results = [BugResult(**row) for row in rows]
    summary = summarise(results)
    print(json.dumps(summary, indent=2))  # noqa: T201
    print("\n| Bug | Python | Status | Blamed | Fix changed | Function | Rank |")  # noqa: T201
    print("|---|---|---|---|---|---|---|")  # noqa: T201
    for r in results:
        print(  # noqa: T201
            f"| {r.bug} | {r.python or ''} | {r.status} | `{r.blamed or '-'}` | "
            f"{'<br>'.join(f'`{f}`' for f in r.truth_functions) or '-'} | "
            f"{'yes' if r.function_hit else 'no'} | {r.rank or '-'} |"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.bugsinpy")
    commands = parser.add_subparsers(dest="command", required=True)
    running = commands.add_parser("run")
    running.add_argument("--dataset", type=Path, required=True)
    running.add_argument("--workspace", type=Path, required=True)
    running.add_argument("--cache", type=Path, required=True)
    running.add_argument("--projects", required=True)
    running.add_argument("--max-per-project", type=int, default=None)
    running.add_argument("--results", type=Path, required=True)
    running.add_argument("--url", default="http://127.0.0.1:8000")
    running.add_argument("--clean", action="store_true", help="delete each bug's trees after")
    reporting = commands.add_parser("report")
    reporting.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else report(args)


if __name__ == "__main__":
    sys.exit(main())
