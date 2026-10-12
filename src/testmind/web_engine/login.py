"""Log in once, so the crawl runs as an authenticated user.

The scan fills a login form and submits it before crawling. The form fields are found by common
selectors, or named explicitly. Success is judged by the password field disappearing (the form
is gone) and, when given, a marker in the URL or page — never by guessing.

The browser actions go through a tiny `LoginDriver` protocol, so the logic is unit-tested with
a fake and the real Playwright page satisfies it. The password is held in a field left out of
the object's `repr`, so it is never printed or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

USERNAME_SELECTOR = (
    "input[type=email], input[name=username], input[name=email], input[name=user], "
    "input[id=username], input[id=email]"
)
PASSWORD_SELECTOR = "input[type=password]"  # noqa: S105 - a CSS selector, not a secret
SUBMIT_SELECTOR = "button[type=submit], input[type=submit], button"


@dataclass(frozen=True, slots=True)
class Credentials:
    login_url: str
    username: str
    password: str = field(repr=False)  # kept out of repr: never logged
    username_selector: str = USERNAME_SELECTOR
    password_selector: str = PASSWORD_SELECTOR
    submit_selector: str = SUBMIT_SELECTOR
    success_contains: str | None = None  # a marker in the URL or page body that proves success


class LoginDriver(Protocol):
    def goto(self, url: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def wait_idle(self) -> None: ...
    def url(self) -> str: ...
    def has(self, selector: str) -> bool: ...
    def body(self) -> str: ...


class LoginError(RuntimeError):
    """The login form could not be filled or the login did not take effect."""


def perform_login(driver: LoginDriver, creds: Credentials) -> None:
    driver.goto(creds.login_url)
    if not driver.has(creds.password_selector):
        raise LoginError(f"No password field at {creds.login_url} ({creds.password_selector})")
    if not driver.has(creds.username_selector):
        raise LoginError(f"No username field at {creds.login_url} ({creds.username_selector})")
    driver.fill(creds.username_selector, creds.username)
    driver.fill(creds.password_selector, creds.password)
    driver.click(creds.submit_selector)
    driver.wait_idle()
    _verify(driver, creds)


def _verify(driver: LoginDriver, creds: Credentials) -> None:
    if creds.success_contains is not None:
        marker = creds.success_contains
        if marker not in driver.url() and marker not in driver.body():
            raise LoginError(f"Logged in, but {marker!r} did not appear (wrong credentials?)")
        return
    # No explicit marker: the login form should be gone.
    if driver.has(creds.password_selector):
        raise LoginError("The password field is still present after submitting (login failed?)")
