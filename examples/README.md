# Example repositories

Small, self-contained projects used to exercise TestMind end to end and as golden fixtures for
the repository analyzer tests (`tests/integration/test_repository_analyzer_examples.py`). By
default this directory is mounted read-only at `/repos` inside the API and worker containers
(`TESTMIND_HOST_REPOS_DIR` in `.env`), so a project can be registered with, for example,
`"source_location": "fastapi_todo"`.

These are fixtures, not part of TestMind itself: they are excluded from TestMind's linting.

| Directory | Stack | What it exercises |
|---|---|---|
| `discount_service/` | Pure Python, pytest, `pyproject.toml` | The `calculate_discount` example from the project brief |
| `django_blog/` | Django, `requirements*.txt` with `-r` include, Django `TestCase` | `manage.py`, `settings.INSTALLED_APPS`, WSGI/ASGI apps, Dockerfile with `ARG` |
| `fastapi_todo/` | FastAPI, `src/` layout, PEP 621 + PEP 735 groups, pytest | App instance, console script, Docker Compose, GitHub Actions |
| `flask_notes/` | Flask app factory, `setup.cfg`, unittest | `create_app()` factory, `console_scripts`, GitLab CI |
| `buggy_inventory/` | Plain Python, **intentionally flawed** | Every code-analysis rule (see its `EXPECTED_FINDINGS.md`), plus code that must *not* be flagged |
| `failing_suite/` | Plain Python, pytest, **intentionally failing** | Every failure category of the failure analysis: code and library exceptions, assertions, fixture, import and syntax errors, a strict xfail that passes (see its `EXPECTED_FAILURES.md`) |
| `shipping_rates/` | Plain Python, pytest, **weak tests** | Mutation testing: every line runs, yet boundary and constant mutants survive (see its `EXPECTED_MUTATION.md`) |
| `buggy_api/` | Flask, pytest, **happy-path tests and API defects** | API testing: endpoint discovery, API coverage, and the probes that find a 500 and unvalidated input (see its `EXPECTED_API.md`) |
| `web_counter/` | Flask, pytest, **Playwright** | Browser tests in the browser sandbox image, and the page's own requests in the API coverage (see its `EXPECTED_BROWSER.md`) |
