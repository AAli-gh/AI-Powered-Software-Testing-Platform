"""A small git history with a known regression, used by the regression engine tests.

base → head:
- `shop/pricing.py::discount` is modified and now wrong (a real regression);
- `shop/pricing.py::tax` only gets a comment and a docstring (not a change);
- `shop/pricing.py::rebate` is added;
- `shop/unused.py::helper` is modified, but no test executes it;
- `shop/cart.py::total` calls `discount`, so its test regresses too without being changed;
- `shop/labels.py` and its test are untouched and must not be selected.
"""

from __future__ import annotations

from pathlib import Path

from tests.gitrepo import make_git_repo

PRICING_BASE = """\
def discount(price, percent):
    return price - price * percent / 100


def tax(price):
    return round(price * 1.2, 2)
"""

PRICING_HEAD = '''\
def discount(price, percent):
    return price - price * percent / 10


def tax(price):
    """Add 20 % VAT."""
    # rounded to cents
    return round(price * 1.2, 2)


def rebate(price):
    return price - 5
'''

FILES = {
    "shop/__init__.py": "",
    "shop/pricing.py": PRICING_BASE,
    "shop/cart.py": (
        "from shop.pricing import discount\n\n\n"
        "def total(prices):\n    return sum(discount(p, 10) for p in prices)\n"
    ),
    "shop/unused.py": "def helper():\n    return 1\n",
    "shop/labels.py": "def label(n):\n    return f'#{n}'\n",
    "tests/__init__.py": "",
    "tests/test_pricing.py": (
        "from shop.pricing import discount, tax\n\n\n"
        "def test_discount():\n    assert discount(100, 10) == 90\n\n\n"
        "def test_tax():\n    assert tax(10) == 12.0\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import total\n\n\n"
        "class TestCart:\n    def test_total(self):\n        assert total([100, 200]) == 270\n"
    ),
    "tests/test_labels.py": (
        "from shop.labels import label\n\n\ndef test_label():\n    assert label(3) == '#3'\n"
    ),
}


def make_shop_repo(root: Path) -> list[str]:
    """Returns [base sha, head sha]; HEAD is the head commit, `v1` tags the base."""
    return make_git_repo(
        root,
        [
            ("Add the shop", FILES),
            (
                "Change pricing",
                {
                    "shop/pricing.py": PRICING_HEAD,
                    "shop/unused.py": "def helper():\n    return 2\n",
                },
            ),
        ],
        tags={"v1": 0},
    )
