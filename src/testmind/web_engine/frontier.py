"""Which URLs to visit next, and which to skip.

The frontier decides the crawl's shape without touching the network: it normalises a URL to a
canonical form (so `/a`, `/a#top` and `/a?` are one page), keeps the crawl inside the network
policy, drops what a browser cannot navigate to (downloads, `mailto:`), and bounds the crawl by
depth and page count. The browser loop (crawler.py) owns the actual navigation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from testmind.web_engine.network_policy import NetworkPolicy

# Suffixes a crawl should not open: a browser would download them, not render a page.
NON_PAGE_SUFFIXES = frozenset(
    {
        ".zip", ".tar", ".gz", ".tgz", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
        ".ico", ".css", ".js", ".mjs", ".json", ".xml", ".csv", ".mp4", ".mp3", ".woff", ".woff2",
        ".ttf", ".eot", ".dmg", ".exe", ".apk", ".wasm",
    }
)  # fmt: skip
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_DEPTH = 5


def normalize(url: str) -> str:
    """A canonical form for deduplication: drop the fragment, an empty query and a trailing
    slash on a non-root path, and lower-case the scheme and host."""
    parts = urlsplit(urldefrag(url).url)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    netloc = host
    if parts.port is not None:
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def is_page(url: str) -> bool:
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    suffix = path[dot:] if dot > path.rfind("/") else ""
    return suffix not in NON_PAGE_SUFFIXES


@dataclass(slots=True)
class Frontier:
    policy: NetworkPolicy
    max_pages: int = DEFAULT_MAX_PAGES
    max_depth: int = DEFAULT_MAX_DEPTH
    _queue: deque[tuple[str, int, str | None]] = field(default_factory=deque)
    _seen: set[str] = field(default_factory=set)
    _dispensed: int = 0

    def add(self, url: str, depth: int, *, from_url: str | None = None) -> bool:
        """Offer a URL (already made absolute). Returns whether it was queued. A refusal by the
        network policy is recorded on it for the report."""
        if depth > self.max_depth or not is_page(url):
            return False
        canonical = normalize(url)
        if canonical in self._seen:
            return False
        reason = self.policy.refusal(canonical)
        if reason is not None:
            self.policy.record(canonical, reason)
            self._seen.add(canonical)  # do not weigh it again
            return False
        self._seen.add(canonical)
        self._queue.append((canonical, depth, from_url))
        return True

    def add_links(self, base_url: str, hrefs: list[str], depth: int) -> int:
        """Queue the links found on `base_url` at the next depth; returns how many were new."""
        added = 0
        for href in hrefs:
            try:
                absolute = urljoin(base_url, href)
            except ValueError:
                continue
            added += self.add(absolute, depth + 1, from_url=base_url)
        return added

    def next(self) -> tuple[str, int, str | None] | None:
        """The next URL to visit, or None when the crawl is done or the page budget is spent."""
        if self._dispensed >= self.max_pages or not self._queue:
            return None
        self._dispensed += 1
        return self._queue.popleft()

    @property
    def seen(self) -> int:
        return len(self._seen)
