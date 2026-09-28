"""The sandbox proxy: a policy-enforcing front for the Docker API.

Only this process holds the Docker socket. The worker talks to it over TCP on a network
reserved for the two of them, with the ordinary Docker CLI (DOCKER_HOST=tcp://…).

Every connection carries exactly one request. The request is parsed and decided by
`policy`, then forwarded with `Connection: close`, and the answer is relayed back. Bytes a
client sends after its request are never forwarded, so a second request cannot slip past
the policy on a kept-alive connection. The only exception is a hijacked `attach`
(Upgrade: tcp), whose stream belongs to a container the proxy already vetted: it is relayed
in both directions until either side closes.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from testmind.core.logging import get_logger
from testmind.sandbox_proxy.policy import (
    MAX_BUILD_CONTEXT,
    Policy,
    RequestDeniedError,
    Route,
    check_build,
    check_build_context,
    check_create,
    check_image,
    is_sandbox_container,
    parse_json,
    route,
)

logger = get_logger(__name__)

MAX_HEAD = 64 * 1024
MAX_JSON_BODY = 1024 * 1024
MAX_SMALL_BODY = 64 * 1024
MAX_INSPECT = 4 * 1024 * 1024
HEAD_TIMEOUT = 30.0
CHUNK = 64 * 1024
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-connection", "te", "trailer", "transfer-encoding",
    "upgrade", "content-length", "expect",
}  # fmt: skip
REASONS = {400: "Bad Request", 403: "Forbidden", 404: "Not Found", 502: "Bad Gateway"}

Opener = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    target: str
    headers: list[tuple[str, str]]

    def header(self, name: str) -> str | None:
        values = [v for k, v in self.headers if k.lower() == name]
        return values[-1] if values else None


def resolve_path(path: str) -> str | None:
    """The real path of a mount source, or None when it does not exist here."""
    if not os.path.lexists(path):
        return None
    return os.path.realpath(path)


class SandboxProxy:
    def __init__(self, policy: Policy, docker: Opener) -> None:
        self._policy = policy
        self._docker = docker

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(read_head(reader), HEAD_TIMEOUT)
            await self._serve(request, reader, writer)
        except RequestDeniedError as exc:
            logger.warning("sandbox_proxy_denied", reason=exc.message, status=exc.status)
            await answer(writer, exc.status, exc.message)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            await close(writer)

    async def _serve(
        self, request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        endpoint, params = route(request.method, request.target)
        upgrade = False
        if endpoint.kind == "create":
            body = await read_body(request, reader, MAX_JSON_BODY)
            config = check_create(params, parse_json(body), self._policy, resolve_path)
            body = json.dumps(config).encode()
        elif endpoint.kind == "build":
            check_build(params, self._policy)
            body = await read_body(request, reader, MAX_BUILD_CONTEXT)
            check_build_context(body, self._policy)
        else:
            if endpoint.kind == "image":
                check_image(endpoint.target, self._policy)
            elif endpoint.kind == "container":
                await self._check_container(endpoint)
                upgrade = endpoint.op == "attach" and (
                    (request.header("upgrade") or "").lower() == "tcp"
                )
            body = await read_body(request, reader, MAX_SMALL_BODY)
        logger.info(
            "sandbox_proxy_allowed", kind=endpoint.kind, op=endpoint.op, target=endpoint.target
        )
        await self._forward(request, body, upgrade, reader, writer)

    async def _check_container(self, endpoint: Route) -> None:
        """Container operations only on containers this proxy created (they carry its label)."""
        status, body = await self._request("GET", f"/containers/{endpoint.target}/json")
        if status == 404:  # noqa: PLR2004
            raise RequestDeniedError(f"No such container: {endpoint.target}", status=404)
        if status != 200:  # noqa: PLR2004
            raise RequestDeniedError("The container could not be inspected", status=502)
        try:
            inspected = json.loads(body)
        except json.JSONDecodeError:
            raise RequestDeniedError("Docker answered an unreadable inspection", 502) from None
        if not isinstance(inspected, dict) or not is_sandbox_container(inspected):
            raise RequestDeniedError("Only sandbox containers can be operated on")

    async def _request(self, method: str, path: str) -> tuple[int, bytes]:
        """A request of the proxy's own to Docker (a fresh connection)."""
        up_reader, up_writer = await self._docker()
        try:
            up_writer.write(f"{method} {path} HTTP/1.1\r\nHost: docker\r\n".encode())
            up_writer.write(b"Connection: close\r\n\r\n")
            await up_writer.drain()
            response = await read_head(up_reader, response=True)
            body = await read_body(response, up_reader, MAX_INSPECT)
            return int(response.target), body
        finally:
            await close(up_writer)

    async def _forward(
        self,
        request: Request,
        body: bytes,
        upgrade: bool,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            up_reader, up_writer = await self._docker()
        except OSError:
            raise RequestDeniedError("The Docker daemon is not reachable", status=502) from None
        try:
            lines = [f"{request.method} {request.target} HTTP/1.1"]
            lines += [f"{k}: {v}" for k, v in request.headers if k.lower() not in HOP_BY_HOP]
            if upgrade:
                lines += ["Connection: Upgrade", "Upgrade: tcp"]
            else:
                lines.append("Connection: close")
            if body or request.method in ("POST", "PUT"):
                lines.append(f"Content-Length: {len(body)}")
            up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
            await up_writer.drain()
            if upgrade:
                await asyncio.gather(relay(reader, up_writer), relay(up_reader, writer))
            else:
                await relay(up_reader, writer)
        finally:
            await close(up_writer)


# ------------------------------------------------------------------------------ HTTP/1.1
async def read_head(reader: asyncio.StreamReader, *, response: bool = False) -> Request:
    """A request head (or, with `response`, a status line: target holds the status code)."""
    try:  # every stream is opened with limit=MAX_HEAD, so an endless head stops here
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise RequestDeniedError("Request head too large", status=400) from None
    try:
        lines = raw[:-4].decode("latin-1").split("\r\n")
        first = lines[0].split(" ")
        if response:
            method, target = "", first[1]
        else:
            method, target, version = first
            if version != "HTTP/1.1" or not method.isalpha():
                raise ValueError(version)
        headers = []
        for line in lines[1:]:
            name, value = line.split(":", 1)
            if not name or name != name.strip() or not name.isascii():
                raise ValueError(name)
            headers.append((name, value.strip()))
    except (ValueError, IndexError):
        raise RequestDeniedError("Malformed HTTP request", status=400) from None
    return Request(method, target, headers)


async def read_body(request: Request, reader: asyncio.StreamReader, limit: int) -> bytes:
    """The whole body (Content-Length or chunked), refused beyond `limit` bytes."""
    encoding = request.header("transfer-encoding")
    length = request.header("content-length")
    if encoding is not None and length is not None:
        raise RequestDeniedError("Both Content-Length and Transfer-Encoding", status=400)
    if encoding is not None:
        if encoding.lower() != "chunked":
            raise RequestDeniedError("Unsupported transfer encoding", status=400)
        return await read_chunked(reader, limit)
    if length is None:
        return b""
    if not length.isdigit():
        raise RequestDeniedError("Invalid Content-Length", status=400)
    if int(length) > limit:
        raise RequestDeniedError("Request body too large", status=400)
    return await reader.readexactly(int(length))


async def read_chunked(reader: asyncio.StreamReader, limit: int) -> bytes:
    body = bytearray()
    while True:
        line = await reader.readuntil(b"\r\n")
        try:
            size = int(line.split(b";")[0].strip(), 16)
        except ValueError:
            raise RequestDeniedError("Malformed chunked body", status=400) from None
        if size == 0:
            while await reader.readuntil(b"\r\n") != b"\r\n":  # trailers
                pass
            return bytes(body)
        if len(body) + size > limit:
            raise RequestDeniedError("Request body too large", status=400)
        body += await reader.readexactly(size)
        if await reader.readexactly(2) != b"\r\n":
            raise RequestDeniedError("Malformed chunked body", status=400)


async def relay(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    """Copy until `source` ends, then half-close `target` (a hijacked stream keeps its
    other direction open, so a container's output is not lost when stdin closes)."""
    try:
        while data := await source.read(CHUNK):
            target.write(data)
            await target.drain()
        target.write_eof()  # TCP and unix streams both support half-closing
    except (ConnectionError, OSError):
        pass


async def answer(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    body = json.dumps({"message": f"testmind sandbox proxy: {message}"}).encode()
    head = (
        f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    try:
        writer.write(head.encode() + body)
        await writer.drain()
    except (ConnectionError, OSError):
        pass


async def close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


def unix_opener(socket_path: str) -> Opener:
    async def open_docker() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_unix_connection(socket_path, limit=MAX_HEAD)

    return open_docker


async def serve(policy: Policy, docker: Opener, host: str, port: int) -> Any:
    proxy = SandboxProxy(policy, docker)
    # Python 3.11's server drops its reference to a connection's task once the client is
    # gone, so a handler still relaying from Docker could be garbage-collected half-way.
    running: set[asyncio.Task[None]] = set()

    def connected(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.create_task(proxy.handle(reader, writer))
        running.add(task)
        task.add_done_callback(running.discard)

    return await asyncio.start_server(connected, host, port, limit=MAX_HEAD)
