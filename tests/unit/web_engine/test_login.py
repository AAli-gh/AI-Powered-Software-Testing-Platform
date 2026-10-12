"""The login logic, with a fake driver: it fills the form, submits, and verifies success."""

from __future__ import annotations

import pytest

from testmind.web_engine.login import (
    PASSWORD_SELECTOR,
    SUBMIT_SELECTOR,
    USERNAME_SELECTOR,
    Credentials,
    LoginError,
    perform_login,
)


class FakeDriver:
    """Records actions; serves a scripted page state before and after submit."""

    def __init__(
        self,
        *,
        present: set[str],
        after_submit: set[str],
        url: str,
        body: str,
        after_url: str | None = None,
    ) -> None:
        self._present = present
        self._after = after_submit
        self._url = url
        self._after_url = after_url
        self._body = body
        self.filled: dict[str, str] = {}
        self.clicked: list[str] = []
        self.submitted = False

    def goto(self, url: str) -> None:
        pass  # the login page is already reflected in the scripted url

    def fill(self, selector: str, value: str) -> None:
        self.filled[selector] = value

    def click(self, selector: str) -> None:
        self.clicked.append(selector)
        self.submitted = True
        self._present = self._after
        if self._after_url is not None:
            self._url = self._after_url

    def wait_idle(self) -> None:
        pass

    def url(self) -> str:
        return self._url

    def has(self, selector: str) -> bool:
        return selector in self._present

    def body(self) -> str:
        return self._body


def creds(**kw: object) -> Credentials:
    base = {"login_url": "https://a.test/login", "username": "me", "password": "secret"}
    return Credentials(**{**base, **kw})  # type: ignore[arg-type]


def logged_in_fields() -> set[str]:
    return {
        USERNAME_SELECTOR,
        PASSWORD_SELECTOR,
        SUBMIT_SELECTOR,
    }


def test_a_successful_login_fills_and_submits() -> None:
    driver = FakeDriver(
        present=logged_in_fields(), after_submit=set(), url="https://a.test/login", body=""
    )  # the form is gone after submit → success
    perform_login(driver, creds())
    assert driver.filled[USERNAME_SELECTOR] == "me"
    assert driver.filled[PASSWORD_SELECTOR] == "secret"
    assert driver.submitted


def test_a_missing_form_is_an_error() -> None:
    no_pw = FakeDriver(
        present={USERNAME_SELECTOR},
        after_submit=set(),
        url="https://a.test/login",
        body="",
    )
    with pytest.raises(LoginError, match="No password field"):
        perform_login(no_pw, creds())
    no_user = FakeDriver(
        present={PASSWORD_SELECTOR},
        after_submit=set(),
        url="https://a.test/login",
        body="",
    )
    with pytest.raises(LoginError, match="No username field"):
        perform_login(no_user, creds())


def test_wrong_credentials_leave_the_form_present() -> None:
    driver = FakeDriver(
        present=logged_in_fields(),
        after_submit=logged_in_fields(),
        url="https://a.test/login",
        body="Invalid password",
    )
    with pytest.raises(LoginError, match="still present"):
        perform_login(driver, creds())


def test_success_marker_in_url_or_body() -> None:
    by_url = FakeDriver(
        present=logged_in_fields(),
        after_submit=logged_in_fields(),
        url="https://a.test/dashboard",
        body="",
    )
    perform_login(by_url, creds(success_contains="/dashboard"))  # marker in URL: ok despite form
    by_body = FakeDriver(
        present=logged_in_fields(),
        after_submit=logged_in_fields(),
        url="https://a.test/login",
        body="Welcome, me",
    )
    perform_login(by_body, creds(success_contains="Welcome"))
    missing = FakeDriver(
        present=logged_in_fields(),
        after_submit=logged_in_fields(),
        url="https://a.test/login",
        body="Invalid",
    )
    with pytest.raises(LoginError, match="did not appear"):
        perform_login(missing, creds(success_contains="Welcome"))


def test_password_is_not_in_the_repr() -> None:
    assert "secret" not in repr(creds())
