"""What the crawler is allowed to open, and what it must refuse.

A scan runs a real browser against a target the user named. Two dangers follow, and this
module is the single gate for both:

1. **Scope creep.** The crawl must stay on the target's own hosts, never wander off to the
   wider web (an external link, an ad, a third-party widget).
2. **SSRF.** A page, a redirect or a link could point the browser at an internal address the
   host can reach but the user never meant to scan: `localhost`, the cloud metadata endpoint
   (`169.254.169.254`), or a private-network host. These are refused unless the user
   explicitly allows internal targets (scanning a site on their own machine).

Everything here is pure and decided from the URL alone; nothing is fetched.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    allowed_hosts: frozenset[str]  # lower-case hostnames the crawl may open
    allow_internal: bool = False  # allow loopback/private/link-local (a site on this machine)
    denied: list[str] = field(default_factory=list, compare=False)  # URLs refused, for the report

    @classmethod
    def for_target(cls, start_url: str, *, allow_internal: bool = False) -> NetworkPolicy:
        host = urlsplit(start_url).hostname
        if host is None:
            raise ValueError(f"The target URL has no host: {start_url!r}")
        return cls(frozenset({host.lower()}), allow_internal=allow_internal)

    def with_hosts(self, *hosts: str) -> NetworkPolicy:
        """A policy that also allows these hosts (e.g. an API host the site's pages call)."""
        extra = frozenset(h.lower() for h in hosts)
        return NetworkPolicy(self.allowed_hosts | extra, allow_internal=self.allow_internal)

    def allows(self, url: str) -> bool:
        return self.refusal(url) is None

    def refusal(self, url: str) -> str | None:
        """Why `url` may not be opened, or None when it may. Deterministic, no DNS, no fetch."""
        try:
            parts = urlsplit(url)
        except ValueError:
            return "the URL could not be parsed"
        if parts.scheme and parts.scheme.lower() not in ALLOWED_SCHEMES:
            return f"scheme {parts.scheme!r} is not http(s)"
        host = parts.hostname
        if not host:
            return "the URL has no host"
        host = host.lower()
        if host not in self.allowed_hosts:
            return f"host {host!r} is not in the scan's allowed hosts"
        if not self.allow_internal and (literal := _internal_ip(host)) is not None:
            return f"{host!r} resolves to the internal address {literal}; internal scans are off"
        return None

    def record(self, url: str, reason: str) -> None:
        line = f"{url} — {reason}"
        if line not in self.denied:
            self.denied.append(line)


def _internal_ip(host: str) -> str | None:
    """The address `host` names, when it is an IP literal that is loopback, private, link-local,
    reserved or unspecified. A bare name (`example.com`) is not judged here: the browser is
    additionally sandboxed, and a name-to-internal-IP rebind is blocked at the network layer."""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return _internal_name(host)
    unsafe = (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )
    return str(address) if unsafe else None


LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})


def _internal_name(host: str) -> str | None:
    """Host names that always mean the local machine, regardless of DNS."""
    if host in LOCAL_NAMES or host.endswith(".localhost"):
        return host
    return None
