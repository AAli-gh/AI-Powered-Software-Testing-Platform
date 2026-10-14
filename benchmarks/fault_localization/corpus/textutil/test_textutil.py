from textutil import count_vowels, initials, is_palindrome, repeat, truncate


def test_is_palindrome() -> None:
    assert is_palindrome("Anna") is True
    assert is_palindrome("hello") is False


def test_count_vowels() -> None:
    assert count_vowels("hello") == 2
    assert count_vowels("xyz") == 0


def test_truncate_short() -> None:
    assert truncate("hi", 5) == "hi"


def test_truncate_long() -> None:
    assert truncate("hello world", 5) == "hello…"


def test_initials() -> None:
    assert initials("ada lovelace") == "AL"


def test_repeat() -> None:
    assert repeat("ab", 3) == "ababab"


def test_repeat_negative() -> None:
    assert repeat("ab", -1) == ""
