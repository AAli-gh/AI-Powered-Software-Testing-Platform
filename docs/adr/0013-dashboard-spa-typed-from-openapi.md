# ADR-0013: A single-page dashboard, typed from the API's OpenAPI schema, on one origin

**Status:** Accepted

## Context

Phase 12 needs a dashboard over everything the API already exposes: projects, runs, test
executions, coverage, failures, regressions, mutation testing and pull request analyses. The
options were server-rendered pages inside FastAPI (Jinja2 with HTMX), or a separate
single-page application. The dashboard must not become a second source of truth or drift from
the API.

## Decision

1. **A React + TypeScript single-page application** in `web/`, built with Vite. It is a pure
   client of the public API, so everything it shows can also be obtained with `curl`.
2. **Types generated from the API's own schema.**
   - `scripts/export_openapi.py` writes `web/openapi.json`, and `openapi-typescript` turns it
     into `web/src/api/schema.ts`. Both are committed.
   - CI regenerates both and fails on any difference.
   - `openapi-fetch` checks every call against the paths, parameters and bodies at compile
     time.
3. **One origin.** An unprivileged nginx container serves the built files and proxies
   `/api` to the API service; Vite's development server does the same. There is no CORS
   configuration to get wrong, and a strict Content-Security-Policy (`'self'` only) is
   possible.
4. **Aggregates on the server.** Two read-only endpoints, `/api/v1/overview` and
   `/api/v1/projects/{id}/overview`, compute counts and trends in SQL (totals are extracted
   from JSONB). The browser does not page through every job to build a chart.
5. **Tests at the network boundary.**
   - Component tests replace `fetch` only. The generated client, TanStack Query and the
     application's routes are real.
   - Page fixtures are answers captured from real runs of the examples.
   - A Playwright test drives a real stack.

## Consequences

- A second toolchain (Node, npm) and a third image. CI gains a job for the dashboard and a
  browser step after the smoke test.
- An API change that breaks the dashboard fails the type check, not a user.
- The dashboard can be replaced or complemented (for example by a CLI) without touching the
  backend.
- Without authentication, the dashboard is as open as the API: it must stay on localhost
  until users exist.
