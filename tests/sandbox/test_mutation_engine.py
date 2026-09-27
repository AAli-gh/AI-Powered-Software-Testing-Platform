"""Mutation testing in the real sandbox, on examples with known results."""

from testmind.mutation_engine.models import MutantStatus, MutationReport
from testmind.sandbox.docker_sandbox import DockerSandbox
from tests.sandbox.conftest import mutation_engine as engine
from tests.sandbox.test_suite_runner import EXAMPLES, analyse

SURVIVORS = [
    (3, "weight <= 0", "(weight < 0)"),
    (3, "0", "(1)"),
    (6, "weight > 10", "(weight >= 10)"),
    (6, "10", "(11)"),
    (9, "15", "(16)"),
    (10, "2", "(3)"),
    (15, "total >= 100", "(total > 100)"),
    (15, "100", "(101)"),
]


def test_weak_tests_let_mutants_survive(shipping: MutationReport) -> None:
    totals = shipping.totals
    assert (totals.total, totals.killed, totals.survived, totals.no_coverage) == (20, 12, 8, 0)
    assert (totals.score, totals.covered_score) == (60.0, 60.0)
    survivors = [
        (m.line, m.original, m.replacement)
        for m in shipping.mutants
        if m.status is MutantStatus.SURVIVED
    ]
    assert survivors == SURVIVORS
    assert {f.function: f.score for f in shipping.functions} == {
        "is_free": 50.0,
        "shipping_cost": 62.5,
    }


def test_killed_mutants_name_the_test_that_noticed(shipping: MutationReport) -> None:
    killed = [m for m in shipping.mutants if m.status is MutantStatus.KILLED]
    assert all(m.killed_by and m.killed_by.startswith("tests/test_rates.py::") for m in killed)
    express = next(m for m in killed if m.original == "cost + 15")
    # Only the test that reaches the line runs against the mutant.
    assert (express.tests, express.killed_by) == (1, "tests/test_rates.py::test_express_costs_more")


def test_strong_tests_kill_everything_and_untested_code_is_reported(
    sandbox: DockerSandbox,
) -> None:
    repository, code = analyse(EXAMPLES / "discount_service")
    strong = engine(sandbox).run(EXAMPLES / "discount_service", repository, code)
    assert (strong.totals.total, strong.totals.killed, strong.totals.score) == (5, 5, 100.0)

    repository, code = analyse(EXAMPLES / "buggy_inventory")
    report = engine(sandbox, max_mutants=10).run(EXAMPLES / "buggy_inventory", repository, code)
    assert (report.generated, report.totals.total) == (71, 10)
    assert report.notes == ["71 mutants generated; the 10 riskiest were run"]
    # The riskiest function (price_for: complexity 15, a finding) comes first; no test runs it.
    assert {m.function for m in report.mutants} == {"price_for"}
    assert report.totals.no_coverage == 10
    assert report.totals.score == 0.0
    assert report.totals.covered_score is None
