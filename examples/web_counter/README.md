# web_counter

A Flask page whose button calls a JSON API from the browser, tested end to end with
Playwright (`pytest-playwright`) against the application on a local port. It exercises
TestMind's browser test support (Phase 10): Playwright detection, the browser sandbox image,
and API coverage of the requests the page sends. Expected results are in
[EXPECTED_BROWSER.md](EXPECTED_BROWSER.md).
