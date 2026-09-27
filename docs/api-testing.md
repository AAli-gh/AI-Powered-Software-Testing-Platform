# API and browser testing

Phase 10 answers three questions about a web application's HTTP API:
- **What endpoints exist?** Found statically, then confirmed by the running application.
- **Which of them do the tests actually call, and how?** Only successful requests, only errors,
  or not at all.
- **Does the API reject invalid input, or crash?** Checked by deterministic probes.

It also writes the missing API tests with an LLM, keeping only the ones the sandbox proves,
and runs Playwright browser tests in a browser sandbox image.

Code:
- `src/testmind/api_engine/`: discovery, paths, capture, coverage, probes, suggester.
- The sandbox plugin [`docker/testmind_api.py`](../docker/testmind_api.py).
- The browser image [`docker/browser.Dockerfile`](../docker/browser.Dockerfile).

## 1. Endpoint discovery (static)

Every analysis run discovers endpoints from the AST, next to the code analysis (analyzer
version 1.1.0). Nothing is imported or executed.

| Framework | Understood |
|---|---|
| FastAPI | `FastAPI()` and `APIRouter(prefix=…)`; method decorators and `api_route(methods=[…])`; `add_api_route`; `include_router(…, prefix=…)`, including nested routers; `mount(path, sub_application)`; path parameter types from the handler's annotations; a request body when a parameter is a pydantic model of the repository (also through a base class or `Annotated[…]`) or has a `Body()` / `Form()` / `File()` default |
| Flask | `Flask()` and `Blueprint(…, url_prefix=…)`, also created inside an application factory; `route(…, methods=[…])` and the method decorators; `add_url_rule` (also with a `MethodView`); `register_blueprint(…, url_prefix=…)` (the registration prefix replaces the blueprint's own, as in Flask); converters (`<int:id>`, `<path:p>`, …); a request body when the handler reads `request.json` / `get_json()` / `form` / `data` / `files` |
| Django | `ROOT_URLCONF`; `urlpatterns` (also `+=`) with `path`, `re_path` and `url`; `include()` by name or module; function views with `require_GET` / `require_POST` / `require_safe` / `require_http_methods([…])` / DRF `api_view([…])`; class-based views (`View.as_view()`, with the methods the class defines). A view without a method restriction answers every method: `ANY` |

References are resolved across the repository's modules through their imports. For example,
`from .routers import items` followed by `app.include_router(items.router)` is followed into
`routers/items.py`.

Nothing is guessed. Constructs that cannot be resolved statically are reported as `notes`:
- a computed prefix or path;
- an `include()` of something that is not a module;
- a router that no application includes.

Paths of every framework share one canonical form: `/items/{item_id}`. It is used to compare
static and runtime routes and to attribute requests.

An application is recorded with how a test can obtain it: `module:app`, or
`module:create_app()` when the factory needs no arguments.

## 2. Recording what the tests send (runtime)

Every test execution also loads TestMind's second pytest plugin in the sandbox,
`testmind_api`. It wraps each framework's request entry point:
- `Starlette.__call__` and `Route.handle`, for FastAPI and Starlette;
- `Flask.full_dispatch_request`;
- `BaseHandler.get_response` (sync and async), for Django.

For each request it records:
- which test sent it;
- the method;
- the route that answered, as the framework names it (the template, not the concrete path);
- the status code, and whether an exception escaped.

It also records the route table of each application that answered a request.

The original entry point is always called, and nothing about the application changes. If the
instrumentation fails (for example on an unexpected framework version), it is reported and
never fails a test. The records are aggregated and capped.

TestMind reads the result as untrusted input: size-capped, schema-validated, strings
bounded, test files only mapped to known locations.

Framework internals are handled explicitly:
- FastAPI ≥ 0.141 matches included routers through an "effective route context" that
  carries the full path. The plugin reads it; with older versions it gets the path from
  `Route.handle`.
- Requests through a Starlette `Mount` get the mount's prefix.
- FastAPI's own `/docs` routes, Flask's static files and Django's admin are marked internal
  and ignored.

## 3. API coverage

The static endpoints and the runtime route table are merged. Each endpoint's `source` is:
- `both`: found statically and registered by the running application;
- `static`: found statically only;
- `runtime`: registered at run time only.

When the application's route table was recorded, the differences between the two lists are
reported as notes (for example, routes added dynamically).

Every recorded request is then attributed to the endpoint whose route answered it. A request
of the project's own tests that no route answered is listed as `unmatched`: a typo in a test,
a removed endpoint, or a wrong method.

| Per endpoint | |
|---|---|
| `tested` | a project test called it |
| `success_tested` / `error_tested` | a project test received a 2xx/3xx / a 4xx from it |
| `server_errors` | 5xx answers or unhandled exceptions, from any test (probes included) |
| `generated_calls` | requests from generated tests and probes (they do not count as tested) |

The endpoint `percent` counts endpoints called by the **project's** tests. **Gaps** are
ranked in this order:
1. `server_error`: the endpoint crashed.
2. `untested`: no test calls it (ties broken by the handler's cyclomatic complexity).
3. `error_only`: only failing requests are tested; the successful path is not.
4. `success_only`: the endpoint has input (path parameters or a body), but no test checks
   that invalid input or a missing resource is rejected.

## 4. Deterministic probes

An execution created with `{"api_probes": true}` also runs a probe module that TestMind
writes from a template (never with an LLM). It covers every FastAPI or Flask application a
test can import:

| Probe | Request | Must be answered |
|---|---|---|
| `unknown_path` | `GET /__testmind_probe__/no-such-path` | 404 |
| `wrong_method` | per path, a method none of its routes accepts | 405 |
| `invalid_param` | a numeric path parameter set to `not-a-number` | 4xx (FastAPI 422, Flask 404) |
| `empty_body` | `{}` to an endpoint that reads a body | 4xx |
| `malformed_body` | invalid JSON to the same endpoint | 4xx |

A probe is skipped when another route would answer its request, because then it would not
test what it claims to (for example, a catch-all route, or `/items/{slug}` next to
`/items/{id}`).

Verdicts come from what the application answered, as recorded by the plugin:

| Verdict | Meaning |
|---|---|
| `passed` | the expected answer |
| `server_error` | a 5xx or an exception: **a bug**. The probe test fails with the real traceback, so the failure analysis groups it like any other failure |
| `accepted_invalid` | 2xx/3xx for invalid input: **the input is not validated** |
| `unexpected` | another answer (for example 400 instead of 404) |
| `not_run` | the probe did not reach the application (the reason is attached) |

Probes run as generated tests, so the execution's outcome includes them, and the report marks
each test as generated or not. Django is not probed: its views need the database that its own
test runner creates.

## 5. AI API tests (on request)

`POST /test-executions/{id}/api-test-suggestions` targets the gaps `untested`, `error_only`
and `success_only`. A `server_error` is a bug to fix, not a test to write.

The model gets:
- the endpoint (method, path, parameters, body);
- what is missing;
- how a test obtains the application;
- the handler's source;
- one of the project's own test modules, for style.

It writes a test module (prompts `write_api_test.v1`, `fix_api_test.v1`). A module is
**accepted** only when:
1. The static checks pass: it parses, has tests, and imports the application; no network,
   process or file-deleting calls.
2. Every test passes in the sandbox against the application as it is.
3. A passing test's request, **as recorded by the application itself**, closes the gap:
   - any request to the endpoint for `untested`;
   - a 2xx/3xx for `error_only`;
   - a 4xx for `success_only`.

Otherwise the concrete problem goes back to the model once. The recorded attempt outcomes
are:
- `rejected` (static checks);
- `fails_on_original`;
- `endpoint_not_called`;
- `gap_not_closed`;
- `sandbox_error`;
- `llm_error`.

The model's own claims are never used. Accepted tests are returned, not written into the
repository.

## 6. Browser tests (Playwright)

The repository analysis detects Playwright from these evidence:
- `playwright` / `pytest-playwright` declared as dependencies;
- `playwright` imported in a test file.

Runner images for such a project are built on the **browser sandbox image**
(`TESTMIND_BROWSER_SANDBOX_IMAGE`, built with `make browser-image`). It is the sandbox image
plus a headless Chromium. Playwright comes from that image, pinned to the browser build, so
the project's own Playwright requirement is not installed again. The install notes say so.

Browser tests run with **the same isolation** as every sandbox run, including no network.
The browser reaches the application that the tests start on `127.0.0.1`. The writable
`/tmp` is 256 MB instead of 64 MB, for Chromium's profile. Requests sent by the page's own
scripts are part of the API coverage.

Without a configured browser image, such a project reports `install_failed` with the reason.

**Scope and verification.** This is minimal support: running a project's existing Playwright
tests. TestMind does not generate browser tests. Building the image downloads Chromium and its
system libraries. The development container of this project cannot reach those download
servers, so the browser test (`tests/sandbox/test_browser.py`) is skipped there, with that
reason. CI builds the image and sets `TESTMIND_REQUIRE_BROWSER_IMAGE=1`, which turns a missing
image into a failure: the browser path is verified on every CI run.

## Verified examples

- **Discovery equals the running application** on `fastapi_todo`, `flask_notes` and
  `django_blog`: every endpoint's `source` is `both`, and no request goes unanswered
  (`tests/sandbox/test_api_testing.py`). The FastAPI example's gaps are real:
  - `DELETE /items/{item_id}` is tested only with a 404;
  - `POST /items/` is never sent an invalid body.
- **[`examples/buggy_api`](../examples/buggy_api)** matches its
  [`EXPECTED_API.md`](../examples/buggy_api/EXPECTED_API.md) exactly:
  - 6 endpoints, 50 % called by the tests;
  - 11 probes, which find the two deliberate defects:
    - `POST /books` with `{}` is a 500 (`KeyError: 'title'`);
    - `PUT` accepts an empty or malformed body.
  - Through the real Celery worker, the probe's crash also appears in the failure analysis.
- **No false alarms:** the well-built examples (`fastapi_todo`, `flask_notes`) pass every probe.
- **AI tests:** with a scripted model in the real sandbox:
  - a test that calls another endpoint is rejected (`endpoint_not_called`);
  - the next attempt is accepted;
  - a test with a wrong expectation is rejected (`fails_on_original`).
- **Browser:** [`examples/web_counter`](../examples/web_counter) is verified in CI (see its
  [`EXPECTED_BROWSER.md`](../examples/web_counter/EXPECTED_BROWSER.md)).

## Limits

- Static discovery covers the documented constructs above. It does not cover:
  - routes registered in loops or through a variable holding the path;
  - DRF routers;
  - Starlette-only applications (these are found at run time).

  The runtime route table shows what was missed, and the notes say so.
- The runtime capture covers requests sent in-process (test clients, or a server thread in
  the test process as in the browser example). Requests to a separately started server
  process are not seen.
- Django endpoints without a method restriction are `ANY`.
- The probes check framework-level behaviour and obvious input validation, not business
  rules.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `TESTMIND_API_MAX_SUGGESTIONS` | 10 | Endpoint gaps per AI API test job |
| `TESTMIND_BROWSER_SANDBOX_IMAGE` | unset | Sandbox image with Chromium (`make browser-image`); required for Playwright projects |

## API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/runs/{id}/api` | Discovered applications (with how a test obtains them), endpoints (method, canonical path, parameters with types, handler, body), notes |
| `POST` | `/api/v1/runs/{id}/test-executions` | Body: `{"api_probes": true}` adds the probes |
| `GET` | `/api/v1/test-executions/{id}/api-coverage` | Frameworks, totals, the first gaps, number of unanswered requests, probe verdict counts, notes. `404` with the reason when there is none |
| `GET` | `…/api-coverage/endpoints` | `tested`, `method`, `source`, `limit` ≤ 500, `offset` |
| `GET` | `…/api-coverage/gaps` | `kind` (`server_error`, `untested`, `error_only`, `success_only`) |
| `GET` | `…/api-coverage/probes` | `verdict` |
| `GET` | `…/api-coverage/unmatched` | Requests no route answered |
| `POST` | `/api/v1/test-executions/{id}/api-test-suggestions` | `202`; `409` no gap a test could close; `503 llm_not_configured` / `dispatch_failed` |
| `GET` | `/api/v1/test-executions/{id}/api-test-suggestions` · `/api/v1/api-test-suggestions/{id}` | Status and `result.suggestions[]`: endpoint, gap, status, test path, code, statuses received, attempts |
