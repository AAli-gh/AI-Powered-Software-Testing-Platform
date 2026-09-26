"""Compare two coverage reports, e.g. the project suite alone vs. with generated tests."""

from __future__ import annotations

from testmind.coverage_engine.models import (
    CoverageDiff,
    CoverageReport,
    FileDelta,
    FunctionDelta,
)


def compare_coverage(base: CoverageReport, head: CoverageReport) -> CoverageDiff:
    base_files = {f.path: f for f in base.files}
    head_files = {f.path: f for f in head.files}
    newly_covered = newly_missed = 0
    files: list[FileDelta] = []
    for path in sorted(base_files.keys() | head_files.keys()):
        before, after = base_files.get(path), head_files.get(path)
        gained = 0
        if before is not None and after is not None:
            # Lines are comparable only when both runs measured the same file.
            base_missing, head_missing = set(before.missing_lines), set(after.missing_lines)
            gained = len(base_missing - head_missing)
            newly_covered += gained
            newly_missed += len(head_missing - base_missing)
        delta = _delta(before.percent if before else None, after.percent if after else None)
        if delta != 0 or gained:
            files.append(
                FileDelta(
                    path=path,
                    base_percent=before.percent if before else None,
                    head_percent=after.percent if after else None,
                    delta=delta,
                    newly_covered_lines=gained,
                )
            )

    base_functions = {(f.path, f.qualname): f.percent for f in base.functions}
    head_functions = {(f.path, f.qualname): f.percent for f in head.functions}
    functions = [
        FunctionDelta(
            path=path,
            qualname=qualname,
            base_percent=base_functions.get((path, qualname)),
            head_percent=head_functions.get((path, qualname)),
            delta=delta,
        )
        for path, qualname in sorted(base_functions.keys() | head_functions.keys())
        if (
            delta := _delta(
                base_functions.get((path, qualname)), head_functions.get((path, qualname))
            )
        )
        != 0
    ]
    return CoverageDiff(
        base_percent=base.totals.percent,
        head_percent=head.totals.percent,
        delta=round(head.totals.percent - base.totals.percent, 2),
        base_totals=base.totals,
        head_totals=head.totals,
        newly_covered_lines=newly_covered,
        newly_missed_lines=newly_missed,
        files=files,
        functions=functions,
    )


def _delta(before: float | None, after: float | None) -> float | None:
    """None when the item exists on one side only (added, removed or unmeasured)."""
    if before is None or after is None:
        return None
    return round(after - before, 2)
