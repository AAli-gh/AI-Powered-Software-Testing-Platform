from pathlib import Path

from testmind.ai_engine.test_generator.targets import select_targets
from testmind.analyzers.code import CodeAnalyzer, CodeProfile
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
