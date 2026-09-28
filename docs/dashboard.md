# Dashboard

Phase 12 adds a web dashboard: a React single-page application in `web/`, served by its own
container next to the API. It is a client of the public API only. Every number, chart and
table comes from an API answer, and nothing is computed or invented in the browser.

## What it shows

| Page | Path | Contents |
|---|---|---|
| Overview | `/` | Projects, active jobs, test executions and pull request analyses. Each project with its latest run, latest tests, coverage and mutation score. Recent executions, recent pull request analyses, and every job table by status |
| Projects | `/projects` | All projects (paged). Register a local repository, or import one from GitHub |
| Project | `/projects/:id` | Latest run, tests, coverage and mutation score. Trends: tests per execution (stacked columns, a column opens its execution), code and API coverage per execution, mutation score per run. Regressions, analysis runs, pull request analyses. Actions: analyse now, compare two commits |
| Run | `/runs/:id` | Summary (code metrics, findings by severity, languages, repository facts), findings (severity filter), functions by complexity, HTTP endpoints, and the run's test executions and mutation runs, which can be started from here (optionally with API probes) |
| Test execution | `/executions/:id` | Tests (outcome filter, messages and tracebacks), coverage (totals, riskiest gaps, files), failure groups, API coverage (probes, endpoints), pytest and install logs |
| Regression | `/regressions/:id` | Verdict, regressed/fixed tests, changed functions, the selection check, per-test changes (filter) |
| Mutation run | `/mutation-runs/:id` | Score, detected and surviving mutants (status filter), weakest functions |
| Pull requests | `/github`, `/github/pull-analyses/:id` | Analyses and their conclusions; request an analysis; the report (regressions, new findings, untested changes) and issue creation from the suggestions |

Jobs that are queued or running are polled every 2 seconds until they finish; finished jobs
are not polled. Tabs, filters and pages live in the URL (`?tab=coverage`), so a view can be
linked.

The API's own error messages are shown as they are: a 404 reads as "not available" with the
reason, other errors as an alert with the code. An unconfigured LLM or GitHub App therefore
appears as the API's `503` message, not as a broken page.

## Architecture

```text
browser ──► web (nginx, :8080) ──► /            static files (the built app)
                              └──► /api, /ready, /health, /docs ──► api (:8000)
```

- **One origin.** nginx serves the app and proxies the API, so there is no CORS and the
  browser never needs the API's address. In development Vite does the same
  (`TESTMIND_API_URL`, default `http://127.0.0.1:8000`).
- **Typed from the API's schema.** `scripts/export_openapi.py` writes `web/openapi.json`
  from the FastAPI app, and `openapi-typescript` generates `web/src/api/schema.ts`.
  `openapi-fetch` then types every path, parameter, body and answer. CI regenerates both
  and fails when they differ from the committed files, so the dashboard cannot drift from
  the API unnoticed.
- **Stack:**
  - React 19, React Router 8 and TanStack Query 5;
  - TypeScript 5.9 in strict mode (with `noUncheckedIndexedAccess` and
    `exactOptionalPropertyTypes`);
  - Vite 8.

  Charts are small SVG components written for this app (`web/src/charts/`), not a chart
  library.
- **Aggregates come from the API.** Two endpoints were added for the dashboard:
  - `GET /api/v1/overview`
  - `GET /api/v1/projects/{id}/overview`

  They count jobs by status and read the trends in SQL. Only the totals are extracted
  from the stored reports, never the whole documents.

### Charts

The charts follow one set of rules:
- **Colours.**
  - The categorical colours are a validated palette (colour-vision-deficiency separation,
    contrast).
  - Status colours (passed, failed, severity) are reserved for states. They always come
    with a label or a legend, never colour alone.
  - Dark mode uses its own colour steps, not an inversion.
- **Marks.** 2px lines, and columns capped at 24px with a rounded top. A missing value
  breaks a line instead of being drawn as zero.
- **Reading values.** One tooltip lists every series at the pointer. The same values can be
  read with the keyboard (arrow keys on a line chart, Tab through columns), and each column
  has a text label for screen readers.
- **Size.** Charts are drawn at their measured width (`ResizeObserver`), so text stays
  legible on a phone.

## Running it

```bash
make up                       # the whole stack, dashboard at http://127.0.0.1:8080
make web-dev                  # development server on :5173 (needs the API on :8000)
make web-check                # lint, format, types, component tests with coverage, build
make openapi                  # after changing the API: regenerate the schema and the types
make web-e2e                  # browser tests against a running stack (TESTMIND_WEB_URL)
```

`TESTMIND_WEB_PORT` changes the published port (default 8080, bound to `127.0.0.1` only).

## Security

- **nginx.** The dashboard's container runs nginx as an unprivileged user on port 8080. Its
  root filesystem is read-only (with a `tmpfs` `/tmp`), every capability is dropped, and
  `no-new-privileges` is set.
- **Headers.** Pages are served with a strict Content-Security-Policy:
  - `default-src 'self'`;
  - no inline scripts or styles, no `eval`;
  - `frame-ancestors 'none'`.

  They also carry `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
  The end-to-end test fails on any console error, which is where CSP violations are
  reported.
- **Untrusted text.** Repository content (test names, messages, tracebacks, file paths) is
  rendered as text by React. Nothing is inserted as HTML.
- **No authentication yet.** Like the API, the dashboard must stay on localhost or a trusted
  network until authentication exists (Phase 13 or 14). Anyone who can open it can start
  jobs and, with the GitHub App configured, create issues.

## Tests

| Suite | Where | What |
|---|---|---|
| Component tests | `web/src/**/*.test.ts(x)`, Vitest + Testing Library + jsdom | Every page through the application's own routes, with the API replaced at the network boundary (`fetch`) by answers typed against the schema. Page tests use documents captured from real runs of the examples (`web/src/test/captured/`). Loading, empty, error and polling states, filters, forms and their request bodies, navigation, keyboard use of tabs and charts, the theme. The coverage gate is 85 % lines, 80 % branches (currently about 96 % and 86 %) |
| End-to-end | `web/e2e/`, Playwright + Chromium | Against a real stack: register a project, analyse it, run its tests (passed with a sandbox, `sandbox unavailable` without one), follow the results to the project trend and the overview, and check that API refusals are shown. It fails on any console error, including CSP violations. CI runs it through the web container after the smoke test |
| Backend | `tests/api/test_overview_api.py` | The two aggregate endpoints on seeded rows: counts, ordering, JSON totals, empty platform, 404 |

## Limits

- The dashboard reads and starts jobs. It does not delete projects or edit anything else;
  the API remains the complete interface.
- Test generation and AI suggestions are not started from the dashboard yet. Their results
  are available in the API.
- Trends show the last 50 executions and mutation runs of a project. There is no date-range
  filter.
- Updates arrive by polling every 2 seconds while a job runs, not by server push.
