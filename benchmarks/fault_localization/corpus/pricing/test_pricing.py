from pricing import (
    cheapest,
    discount,
    free_shipping,
    line_total,
    with_tax,
    within_budget,
)


def test_discount() -> None:
    assert discount(100.0, 10.0) == 90.0


def test_line_total() -> None:
    assert line_total(2.5, 4) == 10.0


def test_with_tax() -> None:
    assert with_tax(100.0, 0.2) == 120.0


def test_within_budget() -> None:
    assert within_budget(50.0, 100.0) is True
    assert within_budget(150.0, 100.0) is False


def test_cheapest() -> None:
    assert cheapest([3.0, 1.0, 2.0]) == 1.0


def test_free_shipping() -> None:
    assert free_shipping(100.0, 50.0) is True
    assert free_shipping(20.0, 50.0) is False
