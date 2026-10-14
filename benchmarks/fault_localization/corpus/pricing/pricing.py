"""A tiny pricing module for the fault-localisation training corpus."""

from __future__ import annotations


def discount(price: float, percent: float) -> float:
    return price * (1 - percent / 100)


def line_total(unit_price: float, quantity: int) -> float:
    return unit_price * quantity


def with_tax(amount: float, rate: float) -> float:
    return amount + amount * rate


def within_budget(amount: float, budget: float) -> bool:
    return amount <= budget


def cheapest(prices: list[float]) -> float:
    best = prices[0]
    for price in prices[1:]:
        if price < best:
            best = price
    return best


def free_shipping(total: float, threshold: float) -> bool:
    return total >= threshold
