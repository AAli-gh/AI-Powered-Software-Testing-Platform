# ADR-0011: Observe API usage inside the application, and cross-check static discovery with it

**Status:** Accepted

## Context

Phase 10 needs to know which HTTP endpoints a test suite exercises, and how. Two obvious
approaches are unreliable:
- **Reading the test code** (`client.get("/items/1")`) misses URLs built at run time, helper
  functions, fixtures and parametrisation. It also cannot tell which route answered a
  concrete path.
- **Relying on static endpoint discovery alone** misses routes registered dynamically. A
  report built only from it would present gaps that do not exist, or hide real ones.

## Decision

1. **Record requests where the application receives them.** A pytest plugin in the sandbox
   image (`docker/testmind_api.py`) wraps each framework's request entry point:
   - `Starlette.__call__` and `Route.handle`;
   - `Flask.full_dispatch_request`;
   - Django's `BaseHandler.get_response`.

   For every request it records the test that sent it, the method, the route the framework
   matched (its template), the status and whether an exception escaped. It also records the
   route table of every application that answered.
2. **Never change behaviour.** The original entry point is always called. An
   instrumentation failure is reported, never raised. Records are aggregated and capped, and
   TestMind validates them as untrusted input.
3. **Discover endpoints statically as well** (AST, no execution), and compare the two lists
   in one canonical path form. Each endpoint is reported as `static`, `runtime` or `both`,
   and the differences become notes.
4. **Use the same record as the acceptance criterion** for AI-written API tests and as the
   verdict of the deterministic probes. The model's claims and the test's source are never
   used.

## Consequences

- Coverage is attributed by the route that actually answered: no URL parsing, correct with
  path parameters, prefixes and mounts.
- The wrapped entry points are framework internals. Version differences are handled
  explicitly (FastAPI ≥ 0.141 routes included routers through an "effective route
  context"), and the golden tests on the example applications fail when a framework change
  breaks attribution.
- Only in-process requests are observed (test clients, or a server thread in the test
  process). A server started as a separate process is out of reach. This is documented.
- The comparison validates the static discovery on every run: on the three example web
  applications, both lists agree exactly.
