"""Parse the failure records written by the sandbox's `testmind_failures` pytest plugin.

They are produced next to untrusted code, so they are untrusted input: the sandbox caps the
size, the shape is validated here, strings are bounded, and paths are only mapped to known
locations (never opened).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_TEXT = 6000


class FailureCaptureError(ValueError):
    """The failure records are missing required data or are not the plugin's format."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class RawFrame(_Model):
    path: str = Field(max_length=4096)
    line: int
    function: str = Field(max_length=MAX_TEXT)
    code: str = Field(default="", max_length=MAX_TEXT)


class RawException(_Model):
    type: str = Field(max_length=500)
    message: str = Field(max_length=MAX_TEXT)


class RawFailure(_Model):
    phase: str = Field(max_length=20)
    # Test failures
    path: str | None = Field(default=None, max_length=4096)
    name: str | None = Field(default=None, max_length=MAX_TEXT)
    line: int | None = None
    exception: RawException | None = None
    chain: list[RawException] = Field(default_factory=list, max_length=10)
    frames: list[RawFrame] = Field(default_factory=list, max_length=100)
    locals: dict[str, str] = Field(default_factory=dict, max_length=50)
    unexpected_pass: bool = False
    # Collection errors (and strict xpass): pytest's formatted report
    nodeid: str | None = Field(default=None, max_length=4096)
    longrepr: str | None = Field(default=None, max_length=MAX_TEXT)


class RawCapture(_Model):
    version: int
    rootdir: str = Field(max_length=4096)
    failures: list[RawFailure] = Field(max_length=1000)
    dropped: int = 0


def parse_failures_json(text: str) -> RawCapture:
    try:
        capture = RawCapture.model_validate_json(text)
    except ValidationError as exc:
        raise FailureCaptureError(
            f"Not a TestMind failure capture: {exc.error_count()} errors"
        ) from None
    if capture.version != 1:
        raise FailureCaptureError(f"Unsupported failure capture version {capture.version}")
    return capture


# ------------------------------------------------------------------ collection error reports
_ERROR_LINE = re.compile(r"^E\s+([A-Za-z_][\w.]*): ?(.*)$", re.MULTILINE)
_FILE_LINE = re.compile(r'File "([^"]+)", line (\d+)')
_LOCATION_LINE = re.compile(r"^(\S+\.py):(\d+): in ", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class CollectionError:
    exception_type: str | None
    message: str
    path: str | None  # absolute or relative to pytest's rootdir
    line: int | None


def parse_collection_error(longrepr: str) -> CollectionError:
    """pytest reports import and syntax errors during collection as formatted text whose last
    `E   Type: message` line names the exception. A SyntaxError also names the offending file
    (`File "…", line N`); other errors name their frames as `path:line: in …`."""
    errors = _ERROR_LINE.findall(longrepr)
    exception_type, message = errors[-1] if errors else (None, longrepr.strip()[-500:])
    located = _FILE_LINE.findall(longrepr)
    if located:
        path, line = located[-1]
    else:
        frames = _LOCATION_LINE.findall(longrepr)
        path, line = frames[-1] if frames else (None, None)
    return CollectionError(
        exception_type=exception_type,
        message=message.strip(),
        path=path,
        line=int(line) if line is not None else None,
    )
