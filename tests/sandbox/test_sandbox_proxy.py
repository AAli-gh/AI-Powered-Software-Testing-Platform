"""The sandbox proxy in front of the real Docker daemon: TestMind's sandbox works through it
unchanged, and what a compromised worker would try is refused before Docker sees it."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from testmind.analyzers.repository.models import Dependency, DependencyGroup
from testmind.sandbox.docker_sandbox import SECCOMP_PROFILE, DockerSandbox
from testmind.sandbox.models import Outcome
from testmind.sandbox.runner_image import IMAGE_REPOSITORY, RunnerImageBuilder
from testmind.sandbox_proxy.__main__ import policy_from
from testmind.sandbox_proxy.server import serve, unix_opener
from tests.sandbox.conftest import SANDBOX_IMAGE
from tests.unit.analyzers.helpers import make_repo

TMP = os.path.realpath(tempfile.gettempdir())


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The proxy on a free port, configured as in production, and the Docker CLI of this
    process pointed at it."""
    policy = policy_from(
        {"TESTMIND_PROXY_MOUNT_ROOTS": TMP, "TESTMIND_PROXY_BASE_IMAGES": SANDBOX_IMAGE}
    )
    loop = asyncio.new_event_loop()
    socket = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    server = loop.run_until_complete(serve(policy, unix_opener(socket), "127.0.0.1", 0))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    url = f"tcp://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    monkeypatch.setenv("DOCKER_HOST", url)
    monkeypatch.setenv("DOCKER_BUILDKIT", "0")
    yield url

    async def shutdown() -> None:
        # Connections still being relayed (e.g. a killed CLI's attach) are cancelled, so
        # each handler closes its streams before the loop goes away.
        server.close()
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()

    asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=30)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=10)
    loop.close()


def direct(*args: str) -> subprocess.CompletedProcess[str]:
    """The Docker CLI talking to the daemon itself (to check what really happened)."""
    env = {k: v for k, v in os.environ.items() if k != "DOCKER_HOST"}
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )  # fmt: skip


def via_proxy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["docker", *args],  # noqa: S607
        capture_output=True, text=True, check=False, timeout=120,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path / "repo",
        {"calc/__init__.py": "", "calc/ops.py": "def div(a, b):\n    return a / b\n"},
    )


@pytest.mark.usefixtures("proxy")
def test_the_sandbox_runs_through_the_proxy_with_its_isolation(repo: Path) -> None:
    sandbox = DockerSandbox(SANDBOX_IMAGE, timeout_seconds=60)
    assert sandbox.available()
    tests = {
        "test_ops.py": (
            "import os, socket, pytest\nfrom calc.ops import div\n\n"
            "def test_ok():\n    assert div(6, 3) == 2\n\n"
            "def test_fails():\n    assert div(1, 0) == 0\n\n"
            "def test_no_network():\n"
            "    with pytest.raises(OSError):\n"
            "        socket.create_connection(('1.1.1.1', 53), timeout=2)\n\n"
            "def test_not_root_and_read_only():\n"
            "    assert os.getuid() == 65534\n"
            "    with pytest.raises(OSError):\n"
            "        open('/workspace/src/calc/pwned.py', 'w')\n"
        )
    }
    result = sandbox.run_pytest(repo, tests)
    assert {t.name: t.outcome for t in result.tests} == {
        "test_ok": Outcome.PASSED,
        "test_fails": Outcome.FAILED,
        "test_no_network": Outcome.PASSED,
        "test_not_root_and_read_only": Outcome.PASSED,
    }
    assert not result.timed_out


@pytest.mark.usefixtures("proxy")
def test_a_timed_out_sandbox_is_killed_through_the_proxy(repo: Path) -> None:
    sandbox = DockerSandbox(SANDBOX_IMAGE, timeout_seconds=5)
    tests = {"test_slow.py": "import time\n\ndef test_slow():\n    time.sleep(60)\n"}
    result = sandbox.run_pytest(repo, tests)
    assert result.timed_out
    running = direct("ps", "--filter", "label=org.testmind.sandbox", "--format", "{{.Names}}")
    assert "testmind-sandbox-" not in running.stdout


@pytest.mark.usefixtures("proxy")
def test_a_runner_image_is_built_through_the_proxy() -> None:
    builder = RunnerImageBuilder(SANDBOX_IMAGE, timeout_seconds=300)
    dependency = Dependency(
        name="iniconfig", specifier="==2.0.0", group=DependencyGroup.RUNTIME,
        source="pyproject.toml",
    )  # fmt: skip
    image = builder.ensure([dependency])
    assert image.tag.startswith(f"{IMAGE_REPOSITORY}:")
    assert direct("image", "inspect", image.tag).returncode == 0


RUN = [
    "run", "--rm", "--network", "none", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges", "--user", "65534:65534", "--read-only",
    "--memory", "128m", "--memory-swap", "128m", "--cpus", "1", "--pids-limit", "64",
]  # fmt: skip


def test_what_a_compromised_worker_would_try_is_refused(proxy: str, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to("/etc")
    name = f"testmind-attack-{uuid.uuid4().hex[:8]}"
    attempts = {
        "privileged": ["run", "--rm", "--name", name, "--privileged", SANDBOX_IMAGE, "true"],
        "network": [*RUN, "--name", name, "--network", "bridge", SANDBOX_IMAGE, "true"],
        "host mount": [
            *RUN, "--name", name, "--mount",
            "type=bind,source=/etc,target=/workspace/etc,readonly", SANDBOX_IMAGE, "true",
        ],
        "symlink mount": [
            *RUN, "--name", name, "--mount",
            f"type=bind,source={link},target=/workspace/etc,readonly", SANDBOX_IMAGE, "true",
        ],
        "root": [*RUN, "--name", name, "--user", "0", SANDBOX_IMAGE, "true"],
        "other image": [*RUN, "--name", name, "busybox:latest", "true"],
        "pull": ["pull", "alpine:3"],
        "list": ["ps", "--all"],
        "volume": ["volume", "create"],
    }  # fmt: skip
    for attempt, args in attempts.items():
        result = via_proxy(*args)
        assert result.returncode != 0, attempt
        assert "testmind sandbox proxy" in result.stderr, (attempt, result.stderr)
    # Nothing was created behind the proxy's back.
    assert direct("ps", "--all", "--filter", f"name={name}", "--quiet").stdout.strip() == ""
    assert proxy.startswith("tcp://127.0.0.1:")


@pytest.mark.usefixtures("proxy")
def test_a_request_to_turn_seccomp_off_gets_testminds_profile() -> None:
    name = f"testmind-seccomp-{uuid.uuid4().hex[:8]}"
    kept = [arg for arg in RUN if arg != "--rm"]  # kept, to inspect it afterwards
    args = [*kept, "--name", name, "--security-opt", "seccomp=unconfined", SANDBOX_IMAGE, "true"]
    try:
        result = via_proxy(*args)
        assert result.returncode == 0, result.stderr
        inspected = direct("inspect", "--format", "{{json .HostConfig.SecurityOpt}}", name)
        options = json.loads(inspected.stdout)
        assert "seccomp=unconfined" not in options
        seccomp = [o for o in options if o.startswith("seccomp=")]
        assert len(seccomp) == 1
        assert json.loads(seccomp[0].removeprefix("seccomp=")) == json.loads(
            SECCOMP_PROFILE.read_text()
        )
    finally:
        direct("rm", "--force", name)


def test_containers_the_proxy_did_not_create_are_out_of_reach(proxy: str) -> None:
    name = f"testmind-bystander-{uuid.uuid4().hex[:8]}"
    started = direct("run", "--detach", "--rm", "--name", name, SANDBOX_IMAGE, "sleep", "60")
    assert started.returncode == 0, started.stderr
    try:
        for args in (["exec", name, "id"], ["rm", "--force", name], ["kill", name],
                     ["inspect", name]):  # fmt: skip
            result = via_proxy(*args)
            assert result.returncode != 0, args
            assert "Only sandbox containers" in result.stderr, (args, result.stderr)
        assert direct("inspect", name).returncode == 0  # still running, untouched
    finally:
        direct("rm", "--force", name)
    assert proxy
