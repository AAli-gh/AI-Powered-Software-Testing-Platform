# Expected browser test results

The golden test is `tests/sandbox/test_browser.py` (browser sandbox image, real Chromium).

- The repository analysis detects **Playwright** (confidence 1.0: `pytest-playwright` and
  `playwright` declared, `playwright` imported in `tests/test_counter.py`), so the runner
  image is built on the browser sandbox image, and Playwright itself is not installed again.
- Both tests pass: `test_page_title[chromium]` and
  `test_clicking_increments_the_counter[chromium]`.
- The container has no network; the browser reaches the application that the tests start on
  `127.0.0.1` (the `base_url` fixture in `tests/conftest.py`).
- API coverage: `GET /` is loaded twice, and `POST /api/increment` is called twice by the
  page's own script after the clicks. Both endpoints are found statically and registered at
  run time (`both`).
