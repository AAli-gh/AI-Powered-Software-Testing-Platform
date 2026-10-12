"""Support for tests that drive a real headless Chromium (the web scanner).

`chromium_path` yields the `executable_path` to give `PlaywrightSession`: an empty string when
Playwright's own bundled browser is installed (CI, after `playwright install chromium`), or a
discovered Chromium in this environment, otherwise the test is skipped — unless
TESTMIND_REQUIRE_BROWSER=1, where a missing browser fails instead (so CI cannot silently skip).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _bundled_works() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:
        return False
    return True


def _discovered() -> str | None:
    explicit = os.environ.get("TESTMIND_CHROMIUM_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    for candidate in sorted(root.glob("chromium-*/chrome-linux/chrome"), reverse=True):
        return str(candidate)
    return None


@pytest.fixture(scope="session")
def chromium_path() -> str:
    pytest.importorskip("playwright", reason="Playwright is not installed")
    if _bundled_works():
        return ""  # PlaywrightSession(executable_path="") → Playwright finds its own browser
    discovered = _discovered()
    if discovered is not None:
        return discovered
    if os.environ.get("TESTMIND_REQUIRE_BROWSER") == "1":
        pytest.fail("No Chromium for Playwright (run `playwright install chromium`)")
    pytest.skip("No Chromium for Playwright (run `playwright install chromium`)")
