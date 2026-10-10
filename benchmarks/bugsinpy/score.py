"""How TestMind did on one bug, and over many.

A bug is **reproduced** when every one of its tests fails on the buggy tree and none fails on
the fixed tree, which really ran its tests. Only reproduced bugs are scored for localisation,
on the failure group that holds the bug's tests (TestMind's deterministic analysis, no LLM):

- **function**: the function TestMind links the failure to is one the fix changed;
- **file**: that function is in a file the fix changed;
- **rank**: position of the first changed function among the group's frames in the project's
  production code, innermost first (frames carry a function's name, not its qualname, so this
  compares the last part of the qualname);
- **category**: TestMind's failure category (`exception_in_code`, `assertion_failure`, ...).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from benchmarks.bugsinpy.client import SuiteRun
from benchmarks.bugsinpy.ground_truth import GroundTruth

REPRODUCED = "reproduced"
TOP_3, TOP_5 = 3, 5
STATUSES = (
    REPRODUCED,
    "not_reproduced",  # the tests ran, but the bug's tests did not fail-then-pass
    "buggy_not_run",  # the buggy tree's tests did not run (install, collection, ...)
    "fixed_not_run",
    "target_not_found",  # the bug's test names match nothing in the tree
    "preparation_error",  # the trees could not be built
)


@dataclass(slots=True)
class BugResult:
    bug: str
    status: str
    detail: str = ""
    python: str | None = None
    targets: list[str] = field(default_factory=list)
    truth_functions: list[str] = field(default_factory=list)
    truth_files: list[str] = field(default_factory=list)
    blamed: str | None = None  # path::qualname of the function TestMind linked
    category: str | None = None
    function_hit: bool = False
    file_hit: bool = False
    rank: int | None = None  # 1-based, among production frames; None: not among them
    seconds: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def matches(node_id: str, target: str) -> bool:
    """Whether a test TestMind reports is one the bug's target names.

    TestMind reports tests as `file::name` (classes left out, as its docs say), so a target in
    a class, `file::Class::test`, is compared as `file::test`. A target naming a whole class,
    `file::TestClass`, matches every test of that file: the class is not in the report.
    """
    file, _, rest = target.partition("::")
    parts = rest.split("::") if rest else []
    if len(parts) == 1 and parts[0][:1].isupper():  # a class: TestClass
        parts = []
    short = "::".join([file, parts[-1]]) if parts else file
    return node_id == short or node_id.startswith((f"{short}[", f"{short}::"))


def _holds(group: dict[str, Any], targets: list[str]) -> bool:
    return any(matches(test["nodeid"], t) for test in group["tests"] for t in targets)


def score(
    bug: str, targets: list[str], truth: GroundTruth, buggy: SuiteRun, fixed: SuiteRun
) -> BugResult:
    result = BugResult(
        bug=bug, status="", python=buggy.python, targets=targets,
        truth_functions=sorted(f"{f.path}::{f.qualname}" for f in truth.functions),
        truth_files=sorted(truth.files), seconds=round(buggy.seconds + fixed.seconds, 1),
    )  # fmt: skip
    if not buggy.ran_tests:
        result.status, result.detail = "buggy_not_run", f"{buggy.outcome}: {buggy.error or ''}"
        return result
    if not fixed.ran_tests:
        result.status, result.detail = "fixed_not_run", f"{fixed.outcome}: {fixed.error or ''}"
        return result
    failing_before = [t for t in targets if any(matches(n, t) for n in buggy.failed)]
    failing_after = [t for t in targets if any(matches(n, t) for n in fixed.failed)]
    if len(failing_before) < len(targets) or failing_after:
        result.status = "not_reproduced"
        result.detail = (
            f"failing before the fix: {len(failing_before)}/{len(targets)}; "
            f"still failing after: {len(failing_after)}"
        )
        return result
    result.status = REPRODUCED
    group = next((g for g in buggy.groups if _holds(g, targets)), None)
    if group is None:
        result.detail = "no failure group holds the bug's tests"
        return result
    result.category = group["category"]
    function = group.get("function")
    truth_names = {(f.path, f.qualname) for f in truth.functions}
    if function:
        result.blamed = f"{function['path']}::{function['qualname']}"
        result.function_hit = (function["path"], function["qualname"]) in truth_names
        result.file_hit = function["path"] in truth.files
    short = {(path, name.rsplit(".", 1)[-1]) for path, name in truth_names}
    seen: list[tuple[str, str]] = []
    for frame in reversed(group.get("frames") or []):  # innermost first
        key = (frame["path"], frame["function"])
        if frame.get("location") == "production" and key not in seen:
            seen.append(key)
    result.rank = next((i + 1 for i, key in enumerate(seen) if key in short), None)
    return result


def summarise(results: list[BugResult]) -> dict[str, Any]:
    statuses = Counter(r.status for r in results)
    scored = [r for r in results if r.status == REPRODUCED]
    n = len(scored)

    def share(count: int) -> float | None:
        return round(100 * count / n, 1) if n else None

    return {
        "bugs": len(results),
        "statuses": {s: statuses.get(s, 0) for s in STATUSES},
        "reproduced": n,
        "function_at_1": share(sum(r.function_hit for r in scored)),
        "file_at_1": share(sum(r.file_hit for r in scored)),
        "rank_at_3": share(sum(r.rank is not None and r.rank <= TOP_3 for r in scored)),
        "rank_at_5": share(sum(r.rank is not None and r.rank <= TOP_5 for r in scored)),
        "no_function_linked": sum(r.blamed is None for r in scored),
        "categories": dict(Counter(r.category or "none" for r in scored).most_common()),
        "python": dict(Counter(r.python or "?" for r in results).most_common()),
    }
