"""A pull request on the shop of tests/regression_fixture.py: the same regression, plus a new
module with a critical finding (eval of input) that no test executes."""

from __future__ import annotations

from tests.regression_fixture import FILES, PRICING_HEAD

BASE = FILES
HEAD = {
    **FILES,
    "shop/pricing.py": PRICING_HEAD,
    "shop/unused.py": "def helper():\n    return 2\n",
    "shop/admin.py": "def run(expression):\n    return eval(expression)\n",
}
