"""Prove someone owns a website before any active (intrusive) test runs against it.

Active security checks send crafted requests, so they must never run against a site the user
cannot prove they control. The platform issues a random token; the owner publishes it at a
well-known path on the site; the platform fetches that path and confirms the token is present.
Only a host verified this way may be scanned actively.

The token and matching logic here are pure (unit-tested); the fetch itself is done by the
service, through the network policy, so it cannot be pointed at an internal host.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

# The owner publishes the token at this path, e.g. https://example.com/.well-known/...
WELL_KNOWN_PATH = "/.well-known/testmind-site-verification.txt"
TOKEN_BYTES = 32
_PREFIX = "testmind-site-verification="


def new_token() -> str:
    """A fresh verification token. The whole string is what the owner publishes."""
    return _PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def normalize_host(value: str) -> str:
    """A bare host or a URL → its lowercased host[:port]; '' if there is none."""
    text = value.strip()
    netloc = urlsplit(text if "://" in text else f"//{text}").netloc
    return netloc.lower()


def verification_urls(host: str) -> list[str]:
    """Where the token may be published, https first (http is tried as a fallback)."""
    return [f"https://{host}{WELL_KNOWN_PATH}", f"http://{host}{WELL_KNOWN_PATH}"]


def token_matches(token: str, fetched: str | None) -> bool:
    """Whether the fetched file proves ownership: it contains the exact token."""
    return bool(token) and fetched is not None and token in fetched
