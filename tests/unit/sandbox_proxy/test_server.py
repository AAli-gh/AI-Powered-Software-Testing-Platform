"""The proxy's HTTP handling, against a stand-in for the Docker daemon on a unix socket.
The real daemon is exercised in tests/sandbox/test_sandbox_proxy.py."""

from __future__ import annotations

import asyncio
import gc
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from testmind.sandbox_proxy.__main__ import ConfigurationError, main, policy_from
from testmind.sandbox_proxy.policy import LABEL, Policy, RequestDeniedError
from testmind.sandbox_proxy.server import read_head, resolve_path, serve, unix_opener

CLI_CREATE = json.loads((Path(__file__).parent / "cli_create.json").read_text())


class FakeDocker:
    """Records every request it receives; answers like the daemon for the endpoints used."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, bytes]] = []
        self.labels: dict[str, dict[str, str]] = {"ours": {LABEL: "true"}, "theirs": {}}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        line = head.split(b"\r\n")[0].decode()
        length = 0
        for header in head.split(b"\r\n")[1:]:
            if header.lower().startswith(b"content-length:"):
                length = int(header.split(b":")[1])
        body = await reader.readexactly(length)
        self.requests.append((line, head + body))
        _method, target, _ = line.split(" ")
        if target.endswith("/json") and "/containers/" in target:
            name = target.split("/")[-2]
            if name not in self.labels:
                await self.reply(writer, 404, {"message": "No such container"})
            elif name == "broken":
                await self.reply(writer, 200, None, raw=b"{not json")
            else:
                await self.reply(writer, 200, {"Id": name, "Config": {"Labels": self.labels[name]}})
            return
        if "/attach" in target:
            writer.write(b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n")
            writer.write(b"output from the container\n")
            await writer.drain()
            data = await reader.read()  # the client's stdin until it half-closes
            writer.write(b"stdin was: " + data)
            await writer.drain()
            writer.close()
            return
        if target.startswith("/_ping") or "/_ping" in target:
            await self.reply(writer, 200, None, raw=b"OK")
            return
        await self.reply(writer, 201, {"Id": "abc", "Warnings": []}, chunked=True)

    @staticmethod
    async def reply(
        writer: asyncio.StreamWriter, status: int, body: Any, *, raw: bytes | None = None,
        chunked: bool = False,
    ) -> None:  # fmt: skip
        data = raw if raw is not None else json.dumps(body).encode()
        if chunked:
            payload = f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n"
            head = f"HTTP/1.1 {status} X\r\nTransfer-Encoding: chunked\r\n\r\n"
        else:
            payload = data
            head = f"HTTP/1.1 {status} X\r\nContent-Length: {len(data)}\r\n\r\n"
        writer.write(head.encode() + payload)
        await writer.drain()
        writer.close()


@pytest.fixture
async def proxy(tmp_path: Path) -> AsyncIterator[tuple[FakeDocker, int, Path]]:
    docker = FakeDocker()
    socket = tmp_path / "docker.sock"
    daemon = await asyncio.start_unix_server(docker.handle, str(socket))
    sandbox = tmp_path / "sandbox"
    (sandbox / "job" / "src").mkdir(parents=True)
    (sandbox / "job" / "out").mkdir()
    policy = Policy(
        images=frozenset({"testmind-sandbox"}), mount_roots=(str(sandbox),),
        base_images=frozenset({"testmind-sandbox:local"}),
    )  # fmt: skip
    server = await serve(policy, unix_opener(str(socket)), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield docker, port, sandbox
    server.close()
    daemon.close()


async def exchange(port: int, request: bytes, *, half_close: bool = False) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(request)
    await writer.drain()
    if half_close:
        writer.write_eof()
    data = await asyncio.wait_for(reader.read(), 5)
    writer.close()
    return data


def create_request(sandbox: Path, *, chunked: bool = False) -> bytes:
    config = json.loads(json.dumps(CLI_CREATE))
    config["HostConfig"]["Mounts"] = [
        {"Type": "bind", "Source": str(sandbox / "job" / "src"), "Target": "/workspace/src",
         "ReadOnly": True},
        {"Type": "bind", "Source": str(sandbox / "job" / "out"), "Target": "/out"},
    ]  # fmt: skip
    body = json.dumps(config).encode()
    head = "POST /v1.54/containers/create?name=testmind-sandbox-1 HTTP/1.1\r\nHost: docker\r\n"
    if chunked:
        half = len(body) // 2
        chunks = (
            f"{half:x};ext=1\r\n".encode() + body[:half] + b"\r\n"
            + f"{len(body) - half:x}\r\n".encode() + body[half:] + b"\r\n0\r\nX-Trailer: 1\r\n\r\n"
        )  # fmt: skip
        return (head + "Transfer-Encoding: chunked\r\n\r\n").encode() + chunks
    return (head + f"Content-Length: {len(body)}\r\n\r\n").encode() + body


async def test_an_allowed_create_is_forwarded_labelled_on_its_own_connection(
    proxy: tuple[FakeDocker, int, Path],
) -> None:
    docker, port, sandbox = proxy
    answer = await exchange(port, create_request(sandbox, chunked=True))
    assert answer.startswith(b"HTTP/1.1 201")
    assert b'"Id": "abc"' in answer
    ((line, raw),) = docker.requests
    assert line == "POST /v1.54/containers/create?name=testmind-sandbox-1 HTTP/1.1"
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"Connection: close" in head
    assert b"Transfer-Encoding" not in head  # re-framed with a Content-Length
    assert json.loads(body)["Labels"] == {LABEL: "true"}


async def test_a_second_request_on_the_same_connection_is_never_forwarded(
    proxy: tuple[FakeDocker, int, Path],
) -> None:
    docker, port, _ = proxy
    smuggled = b"POST /v1.54/containers/theirs/kill HTTP/1.1\r\nHost: d\r\n\r\n"
    answer = await exchange(port, b"HEAD /_ping HTTP/1.1\r\nHost: d\r\n\r\n" + smuggled)
    assert answer.startswith(b"HTTP/1.1 200")
    assert [line for line, _ in docker.requests] == ["HEAD /_ping HTTP/1.1"]


@pytest.mark.parametrize(
    ("request_bytes", "status", "message"),
    [
        (b"GET /v1.54/containers/json HTTP/1.1\r\n\r\n", 403, b"not allowed"),
        (b"POST /v1.54/containers/theirs/start HTTP/1.1\r\n\r\n", 403, b"Only sandbox"),
        (b"POST /v1.54/containers/gone/start HTTP/1.1\r\n\r\n", 404, b"No such container"),
        (b"POST /v1.54/containers/broken/start HTTP/1.1\r\n\r\n", 502, b"unreadable"),
        (b"GET /v1.54/images/alpine:3/json HTTP/1.1\r\n\r\n", 403, b"not a sandbox image"),
        (b"GET / HTTP/1.0\r\n\r\n", 400, b"Malformed"),
        (b"NOT-HTTP\r\n\r\n", 400, b"Malformed"),
        (b"GET /_ping HTTP/1.1\r\nbad header\r\n\r\n", 400, b"Malformed"),
        (b"GET /_ping HTTP/1.1\r\n x: y\r\n\r\n", 400, b"Malformed"),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Content-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\n",
            400, b"Both Content-Length",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Transfer-Encoding: gzip\r\n\r\n",
            400, b"Unsupported transfer",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Content-Length: -1\r\n\r\n",
            400, b"Invalid Content-Length",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Content-Length: 99999999\r\n\r\n",
            400, b"too large",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\nzz\r\n",
            400, b"Malformed chunked",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n2\r\nabXX",
            400, b"Malformed chunked",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n200000\r\n",
            400, b"too large",
        ),
        (
            b"POST /v1.54/containers/create?name=testmind-a HTTP/1.1\r\n"
            b"Content-Length: 5\r\n\r\n{nope",
            400, b"not JSON",
        ),
    ],
)  # fmt: skip
async def test_refusals_are_answered_like_the_daemon(
    proxy: tuple[FakeDocker, int, Path], request_bytes: bytes, status: int, message: bytes
) -> None:
    docker, port, _ = proxy
    docker.labels["broken"] = {}
    answer = await exchange(port, request_bytes)
    assert answer.startswith(f"HTTP/1.1 {status}".encode())
    assert b'"message": "testmind sandbox proxy: ' in answer
    assert message in answer
    assert not any("/start" in line and "theirs" in line for line, _ in docker.requests)


async def test_a_head_that_never_ends_is_refused(proxy: tuple[FakeDocker, int, Path]) -> None:
    _, port, _ = proxy
    answer = await exchange(port, b"GET /_ping HTTP/1.1\r\nX: " + b"a" * 70_000 + b"\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 400")


async def test_an_attach_stream_is_relayed_both_ways_until_closed(
    proxy: tuple[FakeDocker, int, Path],
) -> None:
    docker, port, _ = proxy
    request = (
        b"POST /v1.54/containers/ours/attach?stream=1&stdout=1 HTTP/1.1\r\nHost: d\r\n"
        b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\nhello"
    )
    answer = await exchange(port, request, half_close=True)
    assert answer.startswith(b"HTTP/1.1 101")
    assert b"output from the container" in answer
    assert answer.endswith(b"stdin was: hello")  # output after the client closed its side
    forwarded = docker.requests[-1][1]
    assert b"Upgrade: tcp" in forwarded
    assert b"Connection: close" not in forwarded


async def test_an_unreachable_daemon_is_a_502(tmp_path: Path) -> None:
    policy = Policy(images=frozenset({"testmind-sandbox"}), mount_roots=("/sandbox",))
    server = await serve(policy, unix_opener(str(tmp_path / "missing.sock")), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    answer = await exchange(port, b"HEAD /_ping HTTP/1.1\r\n\r\n")
    server.close()
    assert answer.startswith(b"HTTP/1.1 502")


async def test_a_waiting_connection_survives_garbage_collection() -> None:
    """Regression (Python 3.11): once the client's connection is lost, asyncio's server no
    longer references the handler, so a handler still waiting on something nothing else
    references (here the daemon) was garbage-collected half-way."""
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def daemon_that_never_answers() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        entered.set()
        try:
            await asyncio.get_running_loop().create_future()  # referenced by nothing else
        finally:
            exited.set()
        raise AssertionError("unreachable")  # pragma: no cover

    policy = Policy(images=frozenset({"testmind-sandbox"}), mount_roots=("/sandbox",))
    server = await serve(policy, daemon_that_never_answers, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"HEAD /_ping HTTP/1.1\r\n\r\n")
    await writer.drain()
    await asyncio.wait_for(entered.wait(), 5)
    reset(writer)  # the client vanishes (e.g. a killed Docker CLI)
    await asyncio.sleep(0.05)
    gc.collect()
    assert not exited.is_set()  # still waiting, not collected
    server.close()
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.wait_for(exited.wait(), 5)
    del reader


async def test_read_head_refuses_a_head_without_an_end() -> None:
    reader = asyncio.StreamReader(limit=64 * 1024)
    reader.feed_data(b"GET /_ping HTTP/1.1\r\nX: " + b"a" * 200_000)
    with pytest.raises(RequestDeniedError, match="too large") as error:
        await read_head(reader)
    assert error.value.status == 400


async def test_read_head_of_a_response() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
    response = await read_head(reader, response=True)
    assert response.target == "404"
    assert response.header("content-length") == "0"
    assert response.header("missing") is None


def test_resolve_path(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert resolve_path(str(tmp_path / "real")) == str(tmp_path / "real")
    assert resolve_path(str(tmp_path / "link")) == str(tmp_path / "real")
    assert resolve_path(str(tmp_path / "missing")) is None


def test_configuration(tmp_path: Path) -> None:
    profile = tmp_path / "seccomp.json"
    profile.write_text('{"defaultAction": "SCMP_ACT_ERRNO",\n "syscalls": []}')
    policy = policy_from({
        "TESTMIND_PROXY_MOUNT_ROOTS": "/srv/sandbox, /srv/repos",
        "TESTMIND_PROXY_MAX_CPUS": "1.5",
        "TESTMIND_PROXY_SECCOMP_PROFILE": str(profile),
    })  # fmt: skip
    assert policy.mount_roots == ("/srv/sandbox", "/srv/repos")
    assert policy.max_nano_cpus == 1_500_000_000
    assert policy.seccomp_profile == '{"defaultAction":"SCMP_ACT_ERRNO","syscalls":[]}'
    assert "testmind-runner" in policy.images
    for env in [
        {},
        {"TESTMIND_PROXY_MOUNT_ROOTS": "relative"},
        {"TESTMIND_PROXY_MOUNT_ROOTS": "/"},
        {"TESTMIND_PROXY_MOUNT_ROOTS": "/a/../b"},
        {"TESTMIND_PROXY_MOUNT_ROOTS": "/a", "TESTMIND_PROXY_MAX_PIDS": "many"},
        {"TESTMIND_PROXY_MOUNT_ROOTS": "/a", "TESTMIND_PROXY_SECCOMP_PROFILE": "/missing"},
    ]:
        with pytest.raises(ConfigurationError):
            policy_from(env)


async def test_main_serves_until_cancelled(tmp_path: Path) -> None:
    task = asyncio.create_task(
        main({
            "TESTMIND_PROXY_MOUNT_ROOTS": "/srv/sandbox",
            "TESTMIND_PROXY_HOST": "127.0.0.1",
            "TESTMIND_PROXY_PORT": "0",
            "TESTMIND_PROXY_DOCKER_SOCKET": str(tmp_path / "docker.sock"),
        })
    )  # fmt: skip
    await asyncio.sleep(0.2)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def reset(writer: asyncio.StreamWriter) -> None:
    """Close with a TCP reset (SO_LINGER 0), as a crashed client would."""
    import socket  # noqa: PLC0415
    import struct  # noqa: PLC0415

    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    writer.transport.abort()


async def test_clients_that_vanish_are_dropped_quietly(
    proxy: tuple[FakeDocker, int, Path],
) -> None:
    _, port, sandbox = proxy
    # A body cut short.
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(create_request(sandbox)[:-10])
    writer.write_eof()
    assert await asyncio.wait_for(reader.read(), 5) == b""
    writer.close()
    # A head without an end, far beyond the limit: refused (the client may see the 400, or a
    # reset because the proxy closes with the rest of its data unread).
    try:
        answer = await exchange(port, b"GET /_ping HTTP/1.1\r\nX: " + b"a" * 200_000)
    except ConnectionResetError:
        answer = b"HTTP/1.1 400 (reset)"
    assert answer.startswith(b"HTTP/1.1 400")
    # A client that resets while its container's output is relayed, or before its refusal.
    for request in (
        b"POST /v1.54/containers/ours/attach HTTP/1.1\r\nConnection: Upgrade\r\n"
        b"Upgrade: tcp\r\n\r\n",
        b"GET /v1.54/containers/json HTTP/1.1\r\n\r\n",
    ):
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request)
        await writer.drain()
        reset(writer)
    await asyncio.sleep(0.3)
    # The proxy still serves.
    assert (await exchange(port, b"HEAD /_ping HTTP/1.1\r\n\r\n")).startswith(b"HTTP/1.1 200")


async def test_an_inspection_error_is_a_502(proxy: tuple[FakeDocker, int, Path]) -> None:
    docker, port, _ = proxy

    async def failing(writer: asyncio.StreamWriter, status: int, body: Any, **_: Any) -> None:
        await FakeDocker.reply(writer, 500, {"message": "daemon error"})

    docker.reply = failing  # type: ignore[method-assign]
    answer = await exchange(port, b"POST /v1.54/containers/ours/start HTTP/1.1\r\n\r\n")
    assert answer.startswith(b"HTTP/1.1 502")


async def test_a_runner_build_is_checked_then_forwarded(
    proxy: tuple[FakeDocker, int, Path],
) -> None:
    from tests.unit.sandbox_proxy.test_policy import TEMPLATE, context  # noqa: PLC0415

    docker, port, _ = proxy
    archive = context(TEMPLATE)
    head = (
        "POST /v1.54/build?t=testmind-runner%3A0123&version=1 HTTP/1.1\r\nHost: d\r\n"
        f"Content-Type: application/x-tar\r\nContent-Length: {len(archive)}\r\n\r\n"
    )
    answer = await exchange(port, head.encode() + archive)
    assert answer.startswith(b"HTTP/1.1 201")
    assert docker.requests[-1][1].endswith(archive)
    refused = context(TEMPLATE + "RUN id\n")
    head = head.replace(str(len(archive)), str(len(refused)))
    answer = await exchange(port, head.encode() + refused)
    assert answer.startswith(b"HTTP/1.1 403")
    assert b"runner Dockerfile" in answer


def test_testmind_seccomp_profile_is_the_default() -> None:
    policy = policy_from({"TESTMIND_PROXY_MOUNT_ROOTS": "/srv/sandbox"})
    assert policy.seccomp_profile is not None
    profile = json.loads(policy.seccomp_profile)
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
