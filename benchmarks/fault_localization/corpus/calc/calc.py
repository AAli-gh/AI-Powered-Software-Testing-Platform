"""A tiny calculator module for the fault-localisation training corpus."""

from __future__ import annotations


def add(a: int, b: int) -> int:
    return a + b


def sub(a: int, b: int) -> int:
    return a - b


def mul(a: int, b: int) -> int:
    return a * b


def is_even(n: int) -> bool:
    return n % 2 == 0


def clamp(value: int, low: int, high: int) -> int:
    if value < low:
        return low
    if value > high:
        return high
    return value


def sign(n: int) -> int:
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0
