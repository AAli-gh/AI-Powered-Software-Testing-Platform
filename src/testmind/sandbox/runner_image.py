"""Per-project runner images: the sandbox base image plus the project's dependencies.

Dependencies are installed with `pip --only-binary=:all:` — wheels only — so no third-party
`setup.py` is ever executed while building. The install happens in `docker build` (network is
needed there); tests later run from the finished image with no network. Images are cached by a
hash of the base image's *content id* (so rebuilding the base image invalidates them) and the
exact requirement list.

Projects with browser tests (Playwright) are built on the browser sandbox image instead, which
adds a headless Chromium. Playwright itself comes from that image, pinned to the browser
build it was installed with, so the project's own `playwright` / `pytest-playwright`
requirements are not installed again.
"""

from __future__ import annotations

import hashlib
import subprocess  # nosec B404 - only fixed argument lists, never a shell
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from testmind.analyzers.repository.models import Dependency, DependencyGroup
from testmind.core.logging import get_logger

logger = get_logger(__name__)

MAX_LOG = 20_000
DOCKER_PROBE_TIMEOUT = 15
IMAGE_REPOSITORY = "testmind-runner"
INSTALLED_GROUPS = frozenset({DependencyGroup.RUNTIME, DependencyGroup.DEVELOPMENT})
BROWSER_PROVIDED = frozenset({"playwright", "pytest-playwright"})
DOCKERFILE = """\
FROM {base}
USER root
COPY requirements.txt /opt/testmind/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: \\
        -r /opt/testmind/requirements.txt
USER 65534:65534
LABEL org.testmind.runner="1"
"""


@dataclass(frozen=True, slots=True)
class RunnerImage:
    tag: str
    requirements: tuple[str, ...]
    from_cache: bool
    log: str
    notes: tuple[str, ...]


class DependencyInstallError(RuntimeError):
    def __init__(self, message: str, log: str, notes: tuple[str, ...]) -> None:
        super().__init__(message)
        self.log = log
        self.notes = notes


def requirement_lines(dependencies: Iterable[Dependency]) -> tuple[list[str], list[str]]:
    """PEP 508 lines for runtime + development dependencies, and notes about adjustments.

    Specifiers that are not PEP 440 (e.g. Poetry's ^1.2 / ~1.2) are dropped rather than guessed.
    """
    lines: set[str] = set()
    notes: list[str] = []
    for dependency in dependencies:
        if dependency.group not in INSTALLED_GROUPS:
            continue
        extras = f"[{','.join(dependency.extras)}]" if dependency.extras else ""
        marker = f" ; {dependency.marker}" if dependency.marker else ""
        line = f"{dependency.name}{extras}{dependency.specifier}{marker}"
        try:
            Requirement(line)
        except InvalidRequirement:
            notes.append(
                f"{dependency.name}: specifier {dependency.specifier!r} is not PEP 440, "
                "installing the latest wheel instead"
            )
            line = f"{dependency.name}{extras}{marker}"
        lines.add(line)
    return sorted(lines), notes


class RunnerImageBuilder:
    def __init__(
        self,
        base_image: str,
        *,
        browser_image: str | None = None,
        docker_binary: str = "docker",
        timeout_seconds: int = 900,
    ) -> None:
        self._base = base_image
        self._browser = browser_image
        self._docker = docker_binary
        self._timeout = timeout_seconds

    def tag_for(self, requirements: list[str], base_id: str) -> str:
        digest = hashlib.sha256("\n".join([base_id, *requirements]).encode()).hexdigest()
        return f"{IMAGE_REPOSITORY}:{digest[:16]}"

    def ensure(self, dependencies: Iterable[Dependency], *, browser: bool = False) -> RunnerImage:
        """The runner image for these dependencies; `browser`: built on the browser image."""
        base = self._base
        if browser:
            if self._browser is None:
                raise DependencyInstallError(
                    "The project has browser tests, but no browser sandbox image is configured "
                    "(TESTMIND_BROWSER_SANDBOX_IMAGE)",
                    "",
                    (),
                )
            base = self._browser
            dependencies = [d for d in dependencies if d.name not in BROWSER_PROVIDED]
        requirements, notes = requirement_lines(dependencies)
        if browser:
            notes.append(f"Playwright and a headless Chromium come from {base}")
        if not requirements:
            return RunnerImage(base, (), True, "", tuple(notes))
        tag = self.tag_for(requirements, self._base_id(base, notes))
        if self._exists(tag):
            return RunnerImage(tag, tuple(requirements), True, "", tuple(notes))

        with tempfile.TemporaryDirectory(prefix="testmind-runner-") as context:
            (Path(context) / "Dockerfile").write_text(DOCKERFILE.format(base=base))
            (Path(context) / "requirements.txt").write_text("\n".join(requirements) + "\n")
            try:
                completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                    [self._docker, "build", "--progress", "plain", "--tag", tag, context],
                    capture_output=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                raise DependencyInstallError(
                    "Installing dependencies timed out", "", tuple(notes)
                ) from None
            except OSError as exc:  # docker binary missing or not executable
                raise DependencyInstallError(
                    f"Docker is not available: {exc}", "", tuple(notes)
                ) from None
        log = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")[-MAX_LOG:]
        if completed.returncode != 0:
            logger.warning("runner_image_build_failed", tag=tag)
            raise DependencyInstallError(
                "Installing the project's dependencies failed (only wheels are allowed)",
                log,
                tuple(notes),
            )
        logger.info("runner_image_built", tag=tag, requirements=len(requirements))
        return RunnerImage(tag, tuple(requirements), False, log, tuple(notes))

    def _base_id(self, base: str, notes: list[str]) -> str:
        """Content id of the base image: a rebuilt base (e.g. a new pytest or coverage version)
        must not reuse runner images built on the old one."""
        try:
            probe = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                [self._docker, "image", "inspect", "--format", "{{.Id}}", base],
                capture_output=True,
                timeout=DOCKER_PROBE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyInstallError(
                f"Docker is not available: {exc}", "", tuple(notes)
            ) from None
        if probe.returncode != 0:
            raise DependencyInstallError(
                f"Sandbox base image '{base}' is not available",
                probe.stderr.decode("utf-8", errors="replace")[-MAX_LOG:],
                tuple(notes),
            )
        return probe.stdout.decode("utf-8", errors="replace").strip()

    def _exists(self, tag: str) -> bool:
        try:
            probe = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                [self._docker, "image", "inspect", tag],
                capture_output=True,
                timeout=DOCKER_PROBE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0
