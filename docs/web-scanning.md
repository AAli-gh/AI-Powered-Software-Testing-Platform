# Website scanning

Phase 16 crawls a live website and reports the problems a visitor would hit: broken pages,
server errors, dead links, JavaScript errors, failed requests — and, when asked, dead buttons
and forms that break on submit. It needs no source code: it drives a real headless browser
(Playwright/Chromium) against the deployed site.

Code:
- `src/testmind/web_engine/`: the pure logic (network policy, crawl frontier, the crawl loop
  and issue rules, login, interaction judgement) and the Playwright browser (`browser.py`).
- `src/testmind/services/web_scan_service.py` / `web_scan_executor.py`: the background job.
- `src/testmind/api/routes/web_scans.py`: the HTTP API.
- The example target [`examples/buggy_site/`](../examples/buggy_site) and its
  [expected issues](../examples/buggy_site/EXPECTED_ISSUES.md).

## What a scan finds

The crawl starts at the target URL, follows in-scope links up to a page and depth budget, and
derives issues from the facts each page reports:

| Issue | Severity | When |
|---|---|---|
| `server_error` | high | a 5xx response |
| `broken_link` | high | a link that does not load (DNS, connection, timeout) |
| `not_found` | medium | a 4xx response for a link the site offers |
| `js_error` | medium | an uncaught exception in the page |
| `failed_request` | medium | a request the page made that failed |
| `redirect_loop` | medium | more redirects than the cap |
| `dead_button` | medium | a control that does nothing when clicked (`interact`) |
| `form_error` | medium / high | a form that could not submit, or returned 5xx (`interact`) |
| `slow_page` | low | the page took longer than the slow-page threshold |

With `interact=true`, the scan also clicks each **safe** button and submits each form, on its
own fresh page so one click never affects the next. Destructive controls (delete, pay, log out,
…) are recognised by their label and never clicked.

The scan's own intelligence is deterministic: the issues above come from measured facts, not a
language model. External AI is never required for a scan.

## Safety: the network policy

Every request — the crawl's navigations and every sub-request the browser makes (scripts,
images, redirects) — passes the `NetworkPolicy` (`web_engine/network_policy.py`):

- only `http`/`https`;
- only the target's host (and, where configured, explicitly allowed hosts);
- loopback, private, link-local, reserved and other internal addresses are refused, so a scan
  cannot be steered at `127.0.0.1`, cloud metadata (`169.254.169.254`) or an intranet host (SSRF).

The API refuses an internal or non-http target up front with `422 invalid_target`, and the
browser itself aborts any out-of-scope request as a second line of defence. Public scans run
with internal hosts **out of scope**; reaching your own internal sites is the job of the
authorized-target profile (ownership verified), which is a later sub-phase.

## The API

```
POST /api/v1/web-scans         {"target_url": "...", "max_pages": 100, "max_depth": 5, "interact": false}
GET  /api/v1/web-scans         paged list, newest first
GET  /api/v1/web-scans/{id}    status and, once finished, the full SiteScan
```

`POST` returns `202` with a `queued` scan. A background worker runs the crawl in a real browser
and stores the `SiteScan` (pages reached, issues found, hosts refused as out of scope) on the
row; poll the `GET` endpoint until `status` is `succeeded` or `failed`.

## Running the browser

The crawl needs outbound access to the target, so it runs in the worker with a headless
Chromium rather than in the no-network sandbox used for untrusted project code. The browser
binary is found automatically, or by `TESTMIND_CHROMIUM_PATH`. The pure logic is unit-tested
with a fake browser; the real browser is exercised against a local in-process copy of
`examples/buggy_site` (`tests/browser/test_web_scan.py`).
