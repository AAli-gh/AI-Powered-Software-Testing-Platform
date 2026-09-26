from pathlib import Path

from testmind.ai_engine.test_generator.targets import select_targets
from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.coverage_engine import CoverageReport, RawCoverage, build_coverage_report
from testmind.coverage_engine.parser import RawFile
from tests.unit.analyzers.helpers import make_repo

FILES = {
    "shop/pricing.py": (
        "def simple(x):\n    return x\n\n"
        "def complex_one(x):\n    if x:\n        if x > 1:\n            return 2\n    return 0\n\n"
        "def risky(a, b):\n    return a / b\n\n"
        "def _private(): pass\n\n"
        "class Cart:\n    def total(self): return 1\n    @property\n    def size(self): return 0\n"
        "    def __len__(self): return 0\n"
    ),
    "shop/orders.py": "def place(order):\n    return order\n",
    "manage.py": "def main(): pass\n",
    "shop/migrations/0001.py": "def forwards(apps): pass\n",
}


def profile(tmp_path: Path) -> CodeProfile:
    make_repo(tmp_path, FILES)
    return CodeAnalyzer().analyze(tmp_path, list(FILES))


def test_candidates_and_priority(tmp_path: Path) -> None:
    groups, available = select_targets(profile(tmp_path), max_targets=10)
    assert available == 5  # simple, complex_one, risky, Cart.total, place
    assert [(g.path, g.module, [f.qualname for f in g.functions]) for g in groups] == [
        ("shop/orders.py", "shop.orders", ["place"]),
        ("shop/pricing.py", "shop.pricing", ["simple", "complex_one", "risky", "Cart.total"]),
    ]
    assert [f.rule_id for f in groups[1].findings] == ["TM040", "TM024"]  # _private is unused


def test_max_targets_keeps_highest_priority(tmp_path: Path) -> None:
    groups, _ = select_targets(profile(tmp_path), max_targets=2)
    # risky has a finding, complex_one the highest complexity
    assert [f.qualname for g in groups for f in g.functions] == ["complex_one", "risky"]


def test_path_prefix(tmp_path: Path) -> None:
    groups, available = select_targets(profile(tmp_path), max_targets=10, path_prefix="shop/orders")
    assert available == 1
    assert [g.module for g in groups] == ["shop.orders"]


def coverage_report(code: CodeProfile, *, with_orders: bool) -> CoverageReport:
    # pricing.py: complex_one's inner branch (6-7) and all of risky (11) never ran.
    files = {
        "shop/pricing.py": RawFile(
            executed=frozenset({1, 2, 4, 5, 8, 10, 13, 15, 16, 17, 18, 19}),
            missing=frozenset({6, 7, 11}),
            executed_branches=frozenset({(5, 8)}),
            missing_branches=frozenset({(5, 6), (6, 7), (6, 8)}),
        )
    }
    if with_orders:
        files["shop/orders.py"] = RawFile(frozenset({1, 2}), frozenset(), frozenset(), frozenset())
    return build_coverage_report(RawCoverage("coverage.py", True, files), code)


def test_coverage_guided_selection(tmp_path: Path) -> None:
    code = profile(tmp_path)
    groups, available = select_targets(
        code, max_targets=10, coverage=coverage_report(code, with_orders=True)
    )
    # simple and place are fully covered; Cart.total is a one-liner (no statement of its own).
    assert available == 2
    [group] = groups
    assert [f.qualname for f in group.functions] == ["complex_one", "risky"]
    assert group.coverage is not None
    assert {name: data.percent for name, data in group.coverage.items() if data} == {
        "complex_one": 37.5,  # (2 of 4 lines + 1 of 4 branches) / 8
        "risky": 0.0,
    }


def test_riskiest_gap_first_and_unmeasured_modules(tmp_path: Path) -> None:
    code = profile(tmp_path)
    groups, available = select_targets(
        code, max_targets=2, coverage=coverage_report(code, with_orders=False)
    )
    assert available == 3  # orders.py was never imported: `place` counts as uncovered
    # risky: 1.0 x (1 + 2 x TM040) = 3.0 > complex_one: 0.625 x 3 = 1.875 > place: 1.0 x 1
    assert [f.qualname for g in groups for f in g.functions] == ["complex_one", "risky"]
    groups, _ = select_targets(
        code, max_targets=1, coverage=coverage_report(code, with_orders=False)
    )
    assert [f.qualname for g in groups for f in g.functions] == ["risky"]

    groups, _ = select_targets(
        code, max_targets=3, coverage=coverage_report(code, with_orders=False)
    )
    orders = next(g for g in groups if g.path == "shop/orders.py")
    assert orders.coverage == {"place": None}
