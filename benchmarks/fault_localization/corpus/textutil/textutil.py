"""A tiny text-utilities module for the fault-localisation training corpus."""

from __future__ import annotations


def is_palindrome(text: str) -> bool:
    cleaned = text.lower()
    return cleaned == cleaned[::-1]


def count_vowels(text: str) -> int:
    total = 0
    for char in text.lower():
        if char in "aeiou":
            total = total + 1
    return total


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def initials(name: str) -> str:
    parts = name.split()
    return "".join(part[0].upper() for part in parts if part)


def repeat(text: str, times: int) -> str:
    if times < 0:
        return ""
    return text * times
