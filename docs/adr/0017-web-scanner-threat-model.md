# ADR-0017: The web scanner's threat model — one network gate, ownership before active checks

**Status:** Accepted

## Context

TestMind grew a web scanner (phase 16, [web-scanning.md](../web-scanning.md)) that drives a real
headless browser against a live website and, optionally, sends crafted requests to it. A tool
that fetches arbitrary URLs and sends attack-shaped payloads is dangerous in two directions:

- **Server-Side Request Forgery (SSRF).** A scan is told a URL. If the scanner can be steered
  — by the start URL, a redirect, a link, a script, or an injected parameter — to
  `127.0.0.1`, `169.254.169.254` (cloud metadata) or an intranet host, it becomes a tool for
  reaching the operator's own infrastructure.
- **Unauthorized active testing.** Reflected-XSS, SQL-error and open-redirect probes are
  intrusive. Sending them to a site the user does not own is, at best, abuse.

The scanner must make both classes of mistake structurally hard, not merely discouraged.

## Decision

1. **One network policy, applied twice.** `web_engine/network_policy.py` is the single gate.
   It allows only `http`/`https`, only the target's host (plus hosts explicitly added), and
   refuses loopback, private, link-local, reserved, unspecified and multicast addresses by
   parsing the host as an IP and by name (`localhost`, `*.localhost`). It is applied at two
   independent layers: the crawl frontier refuses out-of-scope links before visiting, and the
   browser routes **every** sub-request (navigations, scripts, images, redirects, XHR) through
   the same policy and aborts anything it refuses. The active prober checks the policy before
   each crafted request too. One list of rules; no layer trusts another to have checked.

2. **Public scans never reach internal hosts.** A scan created through the API is built with
   `allow_internal=False`, and an internal or non-`http(s)` target is refused up front with
   `422 invalid_target`. `allow_internal=True` exists only for the in-process test server; no
   API path sets it.

3. **Active checks require proven ownership.** A host must be an `AuthorizedTarget` that is
   `verified` before a scan may run active checks against it. Verification is a classic
   well-known-file challenge: the platform issues a random token, the owner publishes it at
   `/.well-known/testmind-site-verification.txt`, and the platform fetches that path — through
   the same network policy — and confirms the token. The API refuses `active: true` with `409`
   for an unverified host, and the worker **re-checks** ownership at run time (a host can lapse
   between queueing and running). ([ADR-background in web-scanning.md](../web-scanning.md).)

4. **Active checks are non-destructive and bounded.** They are GET only; they inject a unique
   harmless marker (not a working script) or a single quote, and only read the response; they
   skip destructive controls by label; and they are capped (`MAX_PARAMS` per URL,
   `MAX_TOTAL_PROBES` per scan). The judgement is deterministic — no finding depends on an
   external LLM.

5. **The scan runs in the worker, not the no-network sandbox.** A scan needs outbound access to
   its target, which the untrusted-code sandbox forbids by design. Its safety therefore comes
   from the network policy and ownership gate above, not from Docker isolation.

## Consequences

- SSRF has to defeat two independent checks built from one rule set; a bug in the crawl logic
  alone cannot reach an internal host because the browser gate still fires.
- A user cannot, through any API call, point active checks at a host they have not proven they
  own — and cannot keep that permission silently after it lapses, because the worker re-checks.
- The scanner is honest about scope: out-of-scope requests are recorded as `denied` in the
  report rather than silently dropped.
- The well-known-file method needs the site to serve a static path; DNS-TXT verification is a
  possible later method (the model already carries the concept, not just one mechanism).
- Active findings are signatures, not proof of exploitability: a reflected marker shows a
  parameter is unescaped, and a database-error string shows a query broke — each is a lead a
  human confirms, deliberately erring toward false positives over destructive confirmation.
