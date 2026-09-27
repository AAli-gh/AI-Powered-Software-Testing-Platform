"""API surface of a repository (static discovery) and how its tests exercise it."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE")
ANY_METHOD = "ANY"  # Django views without a method restriction answer every method


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Framework(StrEnum):
    FASTAPI = "fastapi"
    STARLETTE = "starlette"  # only from the runtime route table (no static discovery)
    FLASK = "flask"
    DJANGO = "django"


class Source(StrEnum):
    STATIC = "static"  # found by the AST discovery only
    RUNTIME = "runtime"  # registered by the running application only
    BOTH = "both"


class PathParam(_Model):
    name: str
    type: str = Field(description="int, float, str, path, uuid, slug, regex or unknown")


class Handler(_Model):
    module: str
    qualname: str
    path: str | None = Field(description="Repository file of the handler, when known")
    line: int | None


class ApiApp(_Model):
    """An application object: where it is created and how a test can obtain it."""

    framework: Framework
    module: str
    path: str
    line: int
    variable: str | None = Field(description="Module-level variable holding the app")
    factory: str | None = Field(description="Top-level function that creates the app")
    factory_needs_arguments: bool = False

    @property
    def target(self) -> str | None:
        """`module:attribute` (or `module:factory()`), None when a test cannot obtain it."""
        if self.variable is not None and self.factory is None:
            return f"{self.module}:{self.variable}"
        if self.factory is not None and not self.factory_needs_arguments:
            return f"{self.module}:{self.factory}()"
        return None


class Endpoint(_Model):
    framework: Framework
    method: str = Field(description="HTTP method, or ANY")
    path: str = Field(description="Canonical path template, e.g. /items/{item_id}")
    params: list[PathParam]
    handler: Handler | None
    accepts_body: bool = Field(
        description="The handler reads a request body (known for FastAPI models and Flask's "
        "request.json / get_json / form)"
    )
    app: str | None = Field(description="Target of the application serving it (ApiApp.target)")

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


class ApiSurface(_Model):
    apps: list[ApiApp] = Field(default_factory=list)
    endpoints: list[Endpoint] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="What the static discovery could not resolve (dynamic prefixes, …)",
    )


# ------------------------------------------------------------------------------- coverage
class EndpointCoverage(_Model):
    framework: Framework
    method: str
    path: str
    source: Source
    handler: Handler | None
    calls: int = Field(description="Requests from the project's own tests")
    tests: list[str] = Field(description="Project tests that called it")
    statuses: dict[str, int] = Field(description="Status code → requests (project tests)")
    generated_calls: int = Field(description="Requests from generated tests and probes")
    tested: bool
    success_tested: bool = Field(description="A project test received a 2xx or 3xx from it")
    error_tested: bool = Field(description="A project test received a 4xx from it")
    server_errors: int = Field(description="5xx answers or unhandled exceptions (any test)")


class UnmatchedRequest(_Model):
    framework: Framework
    method: str
    path: str
    status: int | None
    tests: list[str]


class ApiGap(_Model):
    method: str
    path: str
    kind: str = Field(description="server_error, untested, error_only or success_only")
    reason: str
    handler: Handler | None
    complexity: int | None


class ApiTotals(_Model):
    endpoints: int
    tested: int
    success_tested: int
    error_tested: int
    percent: float | None = Field(description="Endpoints called by the project's tests, 0-100")


class ApiCoverageReport(_Model):
    frameworks: list[Framework]
    totals: ApiTotals
    endpoints: list[EndpointCoverage]
    unmatched: list[UnmatchedRequest] = Field(
        description="Requests of the project's tests that no route answered (typos in tests, "
        "removed endpoints, wrong methods)"
    )
    gaps: list[ApiGap]
    probes: list[ProbeResult]
    notes: list[str]


class ProbeKind(StrEnum):
    UNKNOWN_PATH = "unknown_path"  # expects 404
    WRONG_METHOD = "wrong_method"  # expects 405
    INVALID_PARAM = "invalid_param"  # a non-numeric value for a numeric path parameter: 4xx
    EMPTY_BODY = "empty_body"  # {} as JSON body: 4xx
    MALFORMED_BODY = "malformed_body"  # invalid JSON: 4xx


class ProbeVerdict(StrEnum):
    PASSED = "passed"  # the expected answer
    SERVER_ERROR = "server_error"  # 5xx or an unhandled exception: a bug
    ACCEPTED_INVALID = "accepted_invalid"  # 2xx/3xx for invalid input: missing validation
    UNEXPECTED = "unexpected"  # another client error than the expected one
    NOT_RUN = "not_run"  # the probe did not reach the application (see detail)


class ProbeResult(_Model):
    test: str = Field(description="Name of the probe test function")
    kind: ProbeKind
    method: str
    path: str = Field(description="The endpoint's path template")
    request: str = Field(description="The request that was sent")
    expected: str
    status: int | None
    verdict: ProbeVerdict
    detail: str | None


ApiCoverageReport.model_rebuild()
