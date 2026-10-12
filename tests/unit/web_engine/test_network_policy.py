"""The scan's network gate: stay on the target's hosts, refuse internal/SSRF addresses."""

from __future__ import annotations

import pytest

from testmind.web_engine.network_policy import NetworkPolicy


def test_for_target_allows_only_the_target_host() -> None:
    policy = NetworkPolicy.for_target("https://shop.example.com/catalog?page=2")
    assert policy.allowed_hosts == frozenset({"shop.example.com"})
    assert policy.allows("https://shop.example.com/cart")
    assert policy.allows("http://shop.example.com/x")  # http too
    assert not policy.allows("https://other.example.com/")  # a different host
    assert not policy.allows("https://evil.com/")


def test_a_url_without_a_host_is_refused() -> None:
    with pytest.raises(ValueError, match="no host"):
        NetworkPolicy.for_target("not-a-url")
    policy = NetworkPolicy.for_target("https://a.test/")
    assert policy.refusal("mailto:x@a.test") == "scheme 'mailto' is not http(s)"
    assert policy.refusal("/relative/path") == "the URL has no host"
    assert policy.refusal("https:///nohost") == "the URL has no host"


def test_non_http_schemes_are_refused() -> None:
    policy = NetworkPolicy.for_target("https://a.test/")
    assert policy.refusal("javascript:alert(1)") == "scheme 'javascript' is not http(s)"
    assert policy.refusal("file:///etc/passwd") == "scheme 'file' is not http(s)"
    assert policy.refusal("ftp://a.test/x") == "scheme 'ftp' is not http(s)"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "0.0.0.0",  # noqa: S104 - test input, not a bind address
        "10.0.0.5",
        "192.168.1.10",
        "172.16.0.1",
        "169.254.169.254",  # cloud metadata
        "[::1]",
        "localhost",
        "db.localhost",
    ],
)
def test_internal_addresses_are_refused_by_default(host: str) -> None:
    policy = NetworkPolicy(frozenset({host.strip("[]").lower()}))
    assert not policy.allows(f"http://{host}/")
    assert "internal" in (policy.refusal(f"http://{host}/") or "")


def test_internal_addresses_allowed_when_asked() -> None:
    policy = NetworkPolicy(frozenset({"127.0.0.1", "localhost"}), allow_internal=True)
    assert policy.allows("http://127.0.0.1:8000/")
    assert policy.allows("http://localhost:3000/app")


def test_a_public_ip_literal_in_scope_is_allowed() -> None:
    policy = NetworkPolicy(frozenset({"93.184.216.34"}))
    assert policy.allows("http://93.184.216.34/")


def test_with_hosts_extends_scope_and_records_refusals() -> None:
    policy = NetworkPolicy.for_target("https://a.test/").with_hosts("API.a.test")
    assert policy.allows("https://api.a.test/v1")  # case-insensitive
    policy.record("https://evil.com/x", "out of scope")
    policy.record("https://evil.com/x", "out of scope")  # de-duplicated
    assert policy.denied == ["https://evil.com/x — out of scope"]


def test_an_unparseable_url_is_refused() -> None:
    policy = NetworkPolicy.for_target("https://a.test/")
    assert policy.refusal("http://[::1") == "the URL could not be parsed"  # bad IPv6 literal
