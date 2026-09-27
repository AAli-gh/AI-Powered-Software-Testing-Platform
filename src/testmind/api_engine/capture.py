"""Parse what the sandbox's `testmind_api` pytest plugin recorded.

It is produced next to untrusted code, so it is untrusted input: the sandbox caps the size,
the shape and every string are bounded here, and test paths are only mapped to known
locations.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_TEXT = 1000


class ApiCaptureError(ValueError):
    """Not the plugin's format."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawCall(_Model):
    test_path: str | None = Field(default=None, max_length=4096)
    test_name: str | None = Field(default=None, max_length=MAX_TEXT)
    framework: str = Field(max_length=20)
    method: str = Field(max_length=20)
    route: str | None = Field(default=None, max_length=MAX_TEXT)
    path: str = Field(max_length=MAX_TEXT)
    status: int | None = None
    exception: bool = False
    count: int = Field(default=1, ge=1)


class RawRoute(_Model):
    path: str = Field(max_length=MAX_TEXT)
    methods: list[str] = Field(max_length=20)
    handler: str | None = Field(default=None, max_length=MAX_TEXT)
    internal: bool = False


class RawApp(_Model):
    framework: str = Field(max_length=20)
    routes: list[RawRoute] = Field(max_length=5000)


class RawApiCapture(_Model):
    version: int
    rootdir: str = Field(max_length=4096)
    calls: list[RawCall] = Field(max_length=10_000)
    apps: list[RawApp] = Field(max_length=100)
    dropped: int = 0
    errors: list[Annotated[str, Field(max_length=MAX_TEXT)]] = Field(
        default_factory=list, max_length=20
    )


def parse_api_json(text: str) -> RawApiCapture:
    try:
        capture = RawApiCapture.model_validate_json(text)
    except ValidationError as exc:
        raise ApiCaptureError(f"Not a TestMind API capture: {exc.error_count()} errors") from None
    if capture.version != 1:
        raise ApiCaptureError(f"Unsupported API capture version {capture.version}")
    return capture
