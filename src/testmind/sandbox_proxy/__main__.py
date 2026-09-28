"""`python -m testmind.sandbox_proxy`: configured from TESTMIND_PROXY_* environment variables.

TESTMIND_PROXY_MOUNT_ROOTS (required): comma-separated host directories that sandbox bind
mounts may come from. They must be mounted into the proxy at the same paths (read-only), so
it can check that a mount source is not a symlink leading elsewhere.
TESTMIND_PROXY_SECCOMP_PROFILE: the profile forced on every container (default: TestMind's,
src/testmind/sandbox/seccomp.json).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from testmind.core.logging import configure_logging, get_logger
from testmind.sandbox.docker_sandbox import SECCOMP_PROFILE
from testmind.sandbox_proxy.policy import Policy
from testmind.sandbox_proxy.server import serve, unix_opener

logger = get_logger(__name__)

DEFAULT_IMAGES = "testmind-sandbox,testmind-sandbox-browser,testmind-runner"
DEFAULT_BASES = "testmind-sandbox:local,testmind-sandbox-browser:local"


class ConfigurationError(Exception):
    pass


def _list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def policy_from(env: Mapping[str, str]) -> Policy:
    roots = _list(env.get("TESTMIND_PROXY_MOUNT_ROOTS", ""))
    if not roots:
        raise ConfigurationError("TESTMIND_PROXY_MOUNT_ROOTS is required")
    for root in roots:
        if not root.startswith("/") or os.path.normpath(root) != root or root == "/":
            raise ConfigurationError(f"Mount root {root!r} must be an absolute directory")
    # Always injected: TestMind's profile unless another one is configured.
    path = env.get("TESTMIND_PROXY_SECCOMP_PROFILE") or str(SECCOMP_PROFILE)
    try:
        profile = json.dumps(json.loads(Path(path).read_text("utf-8")), separators=(",", ":"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Seccomp profile {path!r}: {exc}") from None
    try:
        return Policy(
            images=frozenset(_list(env.get("TESTMIND_PROXY_IMAGES", DEFAULT_IMAGES))),
            base_images=frozenset(_list(env.get("TESTMIND_PROXY_BASE_IMAGES", DEFAULT_BASES))),
            mount_roots=tuple(roots),
            max_memory=int(env.get("TESTMIND_PROXY_MAX_MEMORY", str(4 * 1024**3))),
            max_nano_cpus=int(float(env.get("TESTMIND_PROXY_MAX_CPUS", "4")) * 10**9),
            max_pids=int(env.get("TESTMIND_PROXY_MAX_PIDS", "1024")),
            seccomp_profile=profile,
        )
    except ValueError as exc:
        raise ConfigurationError(f"Invalid limit: {exc}") from None


async def main(env: Mapping[str, str]) -> None:
    policy = policy_from(env)
    host = env.get("TESTMIND_PROXY_HOST", "0.0.0.0")  # noqa: S104  # nosec B104 - private network
    port = int(env.get("TESTMIND_PROXY_PORT", "2375"))
    socket = env.get("TESTMIND_PROXY_DOCKER_SOCKET", "/var/run/docker.sock")
    server = await serve(policy, unix_opener(socket), host, port)
    logger.info(
        "sandbox_proxy_started", host=host, port=port, mount_roots=list(policy.mount_roots),
        images=sorted(policy.images), seccomp=policy.seccomp_profile is not None,
    )  # fmt: skip
    async with server:
        await server.serve_forever()


if __name__ == "__main__":  # pragma: no cover - process entry point
    configure_logging(os.environ.get("TESTMIND_LOG_LEVEL", "INFO"), json=True)
    try:
        asyncio.run(main(os.environ))
    except ConfigurationError as exc:
        sys.stderr.write(f"sandbox proxy: {exc}\n")
        sys.exit(2)
