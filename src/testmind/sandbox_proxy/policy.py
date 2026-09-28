"""What the sandbox proxy lets through to the Docker daemon: pure decisions, no I/O.

The worker may only ask Docker for what TestMind's sandbox needs:
- run a container from a sandbox or runner image, locked down (see `check_create`);
- inspect, wait for, attach to, kill and remove the containers it created that way;
- build a runner image from TestMind's own Dockerfile template and a list of requirements;
- inspect the sandbox images.

Everything else is denied. Unknown request fields fail closed, so a field added to Docker's
API later is refused until it is reviewed.
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

from packaging.requirements import InvalidRequirement, Requirement

from testmind.sandbox.runner_image import DOCKERFILE, IMAGE_REPOSITORY

LABEL = "org.testmind.sandbox"
MAX_BUILD_CONTEXT = 1024 * 1024
MAX_REQUIREMENTS = 500

VERSION = re.compile(r"^/v1\.\d{1,3}(/.*)$")
NAME = re.compile(r"^testmind-[A-Za-z0-9_.-]{1,120}$")
CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
IMAGE_REF = re.compile(r"^([a-z0-9][a-z0-9._-]{0,127})(?::([A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?$")
SIZE = re.compile(r"^(\d+)([kmg]?)$")

CONTAINER_OPS = {
    ("GET", "json"),
    ("POST", "attach"),
    ("POST", "start"),
    ("POST", "wait"),
    ("POST", "kill"),
}


class RequestDeniedError(Exception):
    """The request is refused; `status` is the HTTP status answered to the client."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class Policy:
    images: frozenset[str]  # repositories that may run (tags are free)
    mount_roots: tuple[str, ...]  # host directories bind mounts may come from
    max_memory: int = 4 * 1024**3
    max_nano_cpus: int = 4 * 10**9
    max_pids: int = 1024
    max_tmpfs: int = 1024**3
    seccomp_profile: str | None = None  # compact JSON, injected into every container
    base_images: frozenset[str] = field(default_factory=frozenset)  # FROM of runner builds


@dataclass(frozen=True, slots=True)
class Route:
    kind: str  # ping, version, image, create, container, build
    target: str = ""  # image reference or container id/name
    op: str = ""  # container operation


# --------------------------------------------------------------------------------- routing
def route(method: str, target: str) -> tuple[Route, dict[str, list[str]]]:  # noqa: PLR0911
    """The endpoint of a request target and its query; anything not allowed is denied."""
    path, _, query = target.partition("?")
    if "%" in path or "\\" in path:
        raise RequestDeniedError("Encoded or unusual paths are not allowed")
    versioned = VERSION.match(path)
    if versioned:
        path = versioned.group(1)
    params = parse_qs(query, keep_blank_values=True, max_num_fields=50)
    parts = path.strip("/").split("/")
    match (method, parts):
        case ("GET" | "HEAD", ["_ping"]):
            return Route("ping"), params
        case ("GET", ["version"]):
            return Route("version"), params
        case ("GET", ["images", name, "json"]):
            return Route("image", target=name), params
        case ("POST", ["containers", "create"]):
            return Route("create"), params
        case ("POST", ["build"]):
            return Route("build"), params
        case ("DELETE", ["containers", container]) if CONTAINER.match(container):
            return Route("container", target=container, op="delete"), params
        case (_, ["containers", container, op]) if (method, op) in CONTAINER_OPS and (
            CONTAINER.match(container)
        ):
            return Route("container", target=container, op=op), params
    raise RequestDeniedError(f"{method} {path} is not allowed through the sandbox proxy")


def repository(image: str) -> str:
    """`testmind-runner:abc` -> `testmind-runner`; registries and digests are refused."""
    match = IMAGE_REF.match(image)
    if not match:
        raise RequestDeniedError(f"Image reference {image!r} is not allowed")
    return match.group(1)


def check_image(image: str, policy: Policy) -> None:
    if repository(image) not in policy.images:
        raise RequestDeniedError(f"Image {image!r} is not a sandbox image")


def is_sandbox_container(inspected: Mapping[str, Any]) -> bool:
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    return isinstance(labels, dict) and labels.get(LABEL) == "true"


# ------------------------------------------------------------------------- create container
CONFIG_FREE = {
    "Cmd", "Entrypoint", "Env", "WorkingDir", "AttachStdin", "AttachStdout", "AttachStderr",
    "OpenStdin", "StdinOnce", "Tty", "Hostname", "Domainname", "StopSignal", "StopTimeout",
}  # fmt: skip
CONFIG_CHECKED = {"Image", "User", "Labels", "NetworkingConfig", "Volumes", "HostConfig"}

HOST_EMPTY = {
    "Binds", "CapAdd", "Devices", "DeviceRequests", "DeviceCgroupRules", "Links", "VolumesFrom",
    "ExtraHosts", "Dns", "DnsOptions", "DnsSearch", "GroupAdd", "Sysctls", "PortBindings",
    "Ulimits", "BlkioWeightDevice", "BlkioDeviceReadBps", "BlkioDeviceWriteBps",
    "BlkioDeviceReadIOps", "BlkioDeviceWriteIOps", "PidMode", "UTSMode", "UsernsMode",
    "Cgroup", "CgroupParent", "VolumeDriver", "Isolation", "ContainerIDFile", "CpusetCpus",
    "CpusetMems", "Runtime",
}  # fmt: skip
HOST_FALSE = {"Privileged", "PublishAllPorts", "OomKillDisable"}
HOST_NUMBERS = {
    "CpuShares", "CpuPeriod", "CpuQuota", "CpuRealtimePeriod", "CpuRealtimeRuntime",
    "CpuCount", "CpuPercent", "IOMaximumIOps", "IOMaximumBandwidth", "BlkioWeight",
    "MemoryReservation", "OomScoreAdj", "ShmSize",
}  # fmt: skip
HOST_CHECKED = {
    "NetworkMode", "CapDrop", "ReadonlyRootfs", "SecurityOpt", "Tmpfs", "Mounts", "Memory",
    "MemorySwap", "NanoCpus", "PidsLimit", "AutoRemove", "RestartPolicy", "LogConfig",
    "ConsoleSize", "MemorySwappiness", "IpcMode", "CgroupnsMode", "MaskedPaths",
    "ReadonlyPaths", "Init", "KernelMemory", "KernelMemoryTCP",
}  # fmt: skip


Resolver = Callable[[str], "str | None"]


def check_create(
    params: Mapping[str, list[str]], body: Any, policy: Policy, resolve: Resolver
) -> dict[str, Any]:
    """The create request as it goes to Docker (label and seccomp profile added).

    `resolve` gives a mount source's real path as the proxy sees it (the host directories are
    mounted at the same paths), or None when it does not exist.
    """
    names = params.get("name", [])
    if len(names) != 1 or not NAME.match(names[0]):
        raise RequestDeniedError("Sandbox containers must be named testmind-…")
    if set(params) - {"name"}:
        raise RequestDeniedError(f"Query parameters not allowed: {sorted(set(params) - {'name'})}")
    if not isinstance(body, dict):
        raise RequestDeniedError("The container configuration must be a JSON object")
    config: dict[str, Any] = dict(body)
    for key, value in config.items():
        if key not in CONFIG_FREE | CONFIG_CHECKED and _set(value):
            raise RequestDeniedError(f"Container setting {key!r} is not allowed")
    image = config.get("Image")
    if not isinstance(image, str):
        raise RequestDeniedError("An image is required")
    check_image(image, policy)
    user = config.get("User")
    if not isinstance(user, str) or not user or user.split(":")[0] in {"0", "root"}:
        raise RequestDeniedError("Sandbox containers must run as a non-root user")
    if _set(config.get("Volumes")):
        raise RequestDeniedError("Anonymous volumes are not allowed")
    networking = config.get("NetworkingConfig") or {}
    if not isinstance(networking, dict) or _set(networking.get("EndpointsConfig")):
        raise RequestDeniedError("Network endpoints are not allowed")
    config["Labels"] = {LABEL: "true"}
    config["HostConfig"] = _check_host(config.get("HostConfig"), policy, resolve)
    return config


def _check_host(host: Any, policy: Policy, resolve: Resolver) -> dict[str, Any]:  # noqa: PLR0912
    if not isinstance(host, dict):
        raise RequestDeniedError("HostConfig is required")
    for key, value in host.items():
        allowed = key in HOST_EMPTY | HOST_FALSE | HOST_NUMBERS | HOST_CHECKED
        if not allowed and _set(value):
            raise RequestDeniedError(f"Host setting {key!r} is not allowed")
        if key in HOST_EMPTY | HOST_FALSE and _set(value):
            raise RequestDeniedError(f"Host setting {key!r} must not be set")
        if key in HOST_NUMBERS and value is not None and not _natural(value):
            raise RequestDeniedError(f"Host setting {key!r} has an invalid value")
    if host.get("NetworkMode") != "none":
        raise RequestDeniedError("Sandbox containers must have no network (NetworkMode none)")
    if "ALL" not in (host.get("CapDrop") or []):
        raise RequestDeniedError("Sandbox containers must drop every capability (CapDrop ALL)")
    if host.get("ReadonlyRootfs") is not True:
        raise RequestDeniedError("Sandbox containers must have a read-only root filesystem")
    if host.get("MaskedPaths") is not None or host.get("ReadonlyPaths") is not None:
        raise RequestDeniedError("Docker's default masked and read-only paths must not be changed")
    host["SecurityOpt"] = _security_options(host.get("SecurityOpt"), policy)
    memory = _positive(host.get("Memory"), "Memory")
    if memory > policy.max_memory:
        raise RequestDeniedError(f"Memory above the limit of {policy.max_memory} bytes")
    if host.get("MemorySwap") != memory:
        raise RequestDeniedError("Swap is not allowed (MemorySwap must equal Memory)")
    if _positive(host.get("NanoCpus"), "NanoCpus") > policy.max_nano_cpus:
        raise RequestDeniedError("CPU above the limit")
    if _positive(host.get("PidsLimit"), "PidsLimit") > policy.max_pids:
        raise RequestDeniedError("PidsLimit above the limit")
    if (host.get("RestartPolicy") or {}).get("Name", "") not in ("", "no"):
        raise RequestDeniedError("Restart policies are not allowed")
    if (host.get("LogConfig") or {}).get("Type", "") not in ("", "json-file"):
        raise RequestDeniedError("Log drivers other than the default are not allowed")
    if host.get("IpcMode", "") not in ("", "private", "none"):
        raise RequestDeniedError("Shared IPC namespaces are not allowed")
    if host.get("CgroupnsMode", "") not in ("", "private"):
        raise RequestDeniedError("The host cgroup namespace is not allowed")
    if _set(host.get("KernelMemory")) or _set(host.get("KernelMemoryTCP")):
        raise RequestDeniedError("Kernel memory settings are not allowed")
    _check_tmpfs(host.get("Tmpfs"), policy)
    _check_mounts(host.get("Mounts"), policy, resolve)
    return host


def _security_options(options: Any, policy: Policy) -> list[str]:
    if not isinstance(options, list):
        raise RequestDeniedError("SecurityOpt must include no-new-privileges")
    kept: list[str] = []
    for option in options:
        if not isinstance(option, str):
            raise RequestDeniedError("Invalid security option")
        if option in ("no-new-privileges", "no-new-privileges:true", "no-new-privileges=true"):
            kept.append("no-new-privileges")
        elif option.startswith("seccomp") and policy.seccomp_profile is not None:
            continue  # replaced by the proxy's own profile below
        else:
            raise RequestDeniedError(
                f"Security option {option.split('=')[0].split(':')[0]!r} not allowed"
            )
    if "no-new-privileges" not in kept:
        raise RequestDeniedError("SecurityOpt must include no-new-privileges")
    if policy.seccomp_profile is not None:
        kept.append(f"seccomp={policy.seccomp_profile}")
    return kept


def _check_tmpfs(tmpfs: Any, policy: Policy) -> None:
    if tmpfs in (None, {}):
        return
    if not isinstance(tmpfs, dict) or set(tmpfs) != {"/tmp"}:  # noqa: S108  # nosec B108 - container path
        raise RequestDeniedError("Only a /tmp tmpfs is allowed")
    options = str(tmpfs["/tmp"]).split(",")  # noqa: S108  # nosec B108
    if "noexec" not in options or "nosuid" not in options:
        raise RequestDeniedError("The /tmp tmpfs must be noexec,nosuid")
    sizes = [o.removeprefix("size=") for o in options if o.startswith("size=")]
    if len(sizes) != 1 or _bytes(sizes[0]) > policy.max_tmpfs:
        raise RequestDeniedError("The /tmp tmpfs needs a size within the limit")


def _check_mounts(mounts: Any, policy: Policy, resolve: Resolver) -> None:
    if mounts is None:
        return
    if not isinstance(mounts, list):
        raise RequestDeniedError("Invalid mounts")
    for mount in mounts:
        if not isinstance(mount, dict) or mount.get("Type") != "bind":
            raise RequestDeniedError("Only bind mounts are allowed")
        extra = set(mount) - {"Type", "Source", "Target", "ReadOnly", "BindOptions", "Consistency"}
        if extra:
            raise RequestDeniedError(f"Mount settings not allowed: {sorted(extra)}")
        options = mount.get("BindOptions") or {}
        if options.get("Propagation", "") not in ("", "private", "rprivate") or set(options) - {
            "Propagation",
            "NonRecursive",
        }:
            raise RequestDeniedError("Bind propagation is not allowed")
        source, target = mount.get("Source"), mount.get("Target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise RequestDeniedError("Mounts need a source and a target")
        if not within(source, policy.mount_roots):
            raise RequestDeniedError(f"Mount source {source!r} is outside the sandbox directories")
        # A symlink planted inside an allowed directory must not lead Docker elsewhere.
        if resolve(source) != source:
            raise RequestDeniedError(
                f"Mount source {source!r} does not exist or goes through a symlink"
            )
        writable = target == "/out"
        if not writable and not target.startswith("/workspace/"):
            raise RequestDeniedError(f"Mount target {target!r} is not allowed")
        if posixpath.normpath(target) != target:
            raise RequestDeniedError(f"Mount target {target!r} is not normalised")
        if not writable and mount.get("ReadOnly") is not True:
            raise RequestDeniedError(f"{target} must be mounted read-only")


def within(path: str, roots: Sequence[str]) -> bool:
    """`path` is absolute, normalised and inside one of `roots` (not a root itself)."""
    if not path.startswith("/") or posixpath.normpath(path) != path:
        return False
    return any(path.startswith(root.rstrip("/") + "/") for root in roots)


# ----------------------------------------------------------------------------------- build
BUILD_FREE = {"dockerfile", "t", "version", "rm", "forcerm", "q", "nocache", "networkmode"}


def check_build(params: Mapping[str, list[str]], policy: Policy) -> str:
    """The tag being built; the context is checked separately (`check_build_context`)."""
    unknown = {k for k, v in params.items() if k not in BUILD_FREE and any(v)}
    if unknown:
        raise RequestDeniedError(f"Build parameters not allowed: {sorted(unknown)}")
    if params.get("version", ["1"]) != ["1"]:
        raise RequestDeniedError("Only the classic builder is allowed (set DOCKER_BUILDKIT=0)")
    if params.get("dockerfile", ["Dockerfile"]) != ["Dockerfile"]:
        raise RequestDeniedError("Only the context's Dockerfile can be built")
    if params.get("networkmode", ["default"])[0] not in ("default", "bridge", ""):
        raise RequestDeniedError("Builds use the default network")
    tags = params.get("t", [])
    if len(tags) != 1 or repository(tags[0]) != IMAGE_REPOSITORY:
        raise RequestDeniedError(f"Builds must produce one {IMAGE_REPOSITORY}:… image")
    return tags[0]


def check_build_context(context: bytes, policy: Policy) -> None:
    """Exactly TestMind's runner Dockerfile (on an allowed base) and plain requirements."""
    if len(context) > MAX_BUILD_CONTEXT:
        raise RequestDeniedError("The build context is too large")
    try:
        with tarfile.open(fileobj=io.BytesIO(context), mode="r:*") as tar:
            members = {m.name.removeprefix("./"): m for m in tar.getmembers() if m.name != "."}
            if set(members) != {"Dockerfile", "requirements.txt"} or not all(
                m.isreg() for m in members.values()
            ):
                raise RequestDeniedError(
                    "The build context must hold only Dockerfile and requirements.txt"
                )
            dockerfile = _read(tar, members["Dockerfile"])
            requirements = _read(tar, members["requirements.txt"])
    except tarfile.TarError:
        raise RequestDeniedError("The build context is not a tar archive") from None
    if not any(dockerfile == DOCKERFILE.format(base=base) for base in policy.base_images):
        raise RequestDeniedError("Only TestMind's runner Dockerfile can be built")
    lines = [line for line in requirements.splitlines() if line.strip()]
    if len(lines) > MAX_REQUIREMENTS:
        raise RequestDeniedError("Too many requirements")
    for line in lines:
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            raise RequestDeniedError(f"Invalid requirement line: {line[:80]!r}") from None
        if requirement.url is not None or line.lstrip().startswith("-"):
            raise RequestDeniedError("Requirements must be package names, not URLs or options")


def _read(tar: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    handle = tar.extractfile(member)
    if handle is None:  # pragma: no cover - regular files always have data
        raise RequestDeniedError("Unreadable build context")
    return handle.read(MAX_BUILD_CONTEXT).decode("utf-8", errors="strict")


# --------------------------------------------------------------------------------- helpers
def _natural(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _set(value: Any) -> bool:
    return value not in (None, False, 0, "", [], {})


def _positive(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RequestDeniedError(f"{name} must be set to a positive limit")
    return value


def _bytes(size: str) -> int:
    match = SIZE.match(size.lower())
    if not match:
        raise RequestDeniedError(f"Invalid size {size!r}")
    return int(match.group(1)) * {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}[match.group(2)]


def parse_json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestDeniedError("The request body is not JSON", status=400) from None
