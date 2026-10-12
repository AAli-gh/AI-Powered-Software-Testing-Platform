"""The crawl frontier: URL normalisation, scope, dedup and the depth/page caps. No network."""

from __future__ import annotations

import pytest

from testmind.web_engine.frontier import Frontier, is_page, normalize
from testmind.web_engine.network_policy import NetworkPolicy


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://A.test/a#top", "https://a.test/a"),
        ("https://a.test/a/", "https://a.test/a"),  # trailing slash dropped
        ("https://a.test/", "https://a.test/"),  # except the root
        ("https://a.test", "https://a.test/"),
        ("https://a.test/a?b=1#x", "https://a.test/a?b=1"),  # query kept, fragment dropped
        ("HTTPS://a.test:8000/p", "https://a.test:8000/p"),  # scheme/host lower, port kept
    ],
)
def test_normalize(url: str, expected: str) -> None:
    assert normalize(url) == expected


def test_is_page_skips_downloads_and_assets() -> None:
    assert is_page("https://a.test/report")
    assert is_page("https://a.test/data.php")  # a dynamic page, not an asset
    assert not is_page("https://a.test/manual.pdf")
    assert not is_page("https://a.test/app.js")
    assert not is_page("https://a.test/logo.PNG")  # case-insensitive


def frontier(**kw: object) -> Frontier:
    return Frontier(NetworkPolicy.for_target("https://a.test/"), **kw)  # type: ignore[arg-type]


def test_dedup_and_scope() -> None:
    f = frontier()
    assert f.add("https://a.test/x", 1) is True
    assert f.add("https://a.test/x/", 1) is False  # same page, normalised
    assert f.add("https://a.test/x#frag", 1) is False
    assert f.add("https://other.test/y", 1) is False  # out of scope
    assert "https://other.test/y — host 'other.test' is not in the scan's allowed hosts" in (
        f.policy.denied
    )
    assert f.add("https://a.test/manual.pdf", 1) is False  # not a page


def test_depth_and_page_caps() -> None:
    f = frontier(max_pages=2, max_depth=1)
    assert f.add("https://a.test/", 0) is True
    assert f.add("https://a.test/deep", 2) is False  # beyond max_depth
    assert f.add("https://a.test/b", 1) is True
    assert f.add("https://a.test/c", 1) is True
    first = f.next()
    assert first == ("https://a.test/", 0, None)
    assert f.next() is not None  # second
    assert f.next() is None  # page budget (2) spent, even though more are queued


def test_add_links_resolves_relative_and_counts_new() -> None:
    f = frontier()
    f.add("https://a.test/dir/page", 0)
    f.next()
    added = f.add_links(
        "https://a.test/dir/page",
        ["sub", "/root", "page", "https://evil.test/x", "mailto:a@a.test", "http://["],
        depth=0,
    )
    # /dir/sub and /root are new; 'page' is self, evil is out of scope, mailto refused,
    # 'http://[' is a malformed href that cannot be joined
    assert added == 2
    assert f.seen >= 3
