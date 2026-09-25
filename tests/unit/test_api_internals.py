"""Small pieces of the API layer that are awkward to reach through HTTP."""

from starlette.types import Message, Receive, Scope, Send

from testmind.api.errors import _status_for
from testmind.api.middleware import RequestContextMiddleware
from testmind.core.errors import AppError, InvalidStateTransitionError


def test_unmapped_app_error_is_400() -> None:
    assert _status_for(AppError("generic")) == 400


def test_subclass_inherits_parent_status() -> None:
    assert _status_for(InvalidStateTransitionError("x")) == 409


async def test_middleware_passes_non_http_scopes_through_untouched() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        return None

    scope: Scope = {"type": "lifespan"}
    await RequestContextMiddleware(inner)(scope, receive, send)
    assert seen == [scope]
