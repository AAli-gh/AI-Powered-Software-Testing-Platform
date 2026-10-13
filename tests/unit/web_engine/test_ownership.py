"""Ownership token generation, host normalization and the match rule (pure)."""

from __future__ import annotations

import pytest

from testmind.web_engine.ownership import (
    WELL_KNOWN_PATH,
    new_token,
    normalize_host,
    token_matches,
    verification_urls,
)


def test_tokens_are_unique_and_prefixed() -> None:
    a, b = new_token(), new_token()
    assert a != b
    assert a.startswith("testmind-site-verification=")
    assert len(a) > len("testmind-site-verification=") + 20  # a real random tail


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("example.com", "example.com"),
        ("Example.COM", "example.com"),
        ("https://example.com/path?q=1", "example.com"),
        ("http://example.com:8443/", "example.com:8443"),
        ("  example.com  ", "example.com"),
    ],
)
def test_normalize_host(given: str, expected: str) -> None:
    assert normalize_host(given) == expected


def test_verification_urls_try_https_then_http() -> None:
    urls = verification_urls("example.com")
    assert urls == [
        f"https://example.com{WELL_KNOWN_PATH}",
        f"http://example.com{WELL_KNOWN_PATH}",
    ]


def test_token_matches_only_when_the_exact_token_is_present() -> None:
    token = new_token()
    assert token_matches(token, f"\n{token}\n")  # the file may have surrounding whitespace
    assert token_matches(token, f"some notes\n{token}")
    assert not token_matches(token, "testmind-site-verification=wrong")
    assert not token_matches(token, None)  # nothing fetched
    assert not token_matches("", "anything")  # no token issued
