"""Per-project runner images: the sandbox base image plus the project's dependencies.

Dependencies are installed with `pip --only-binary=:all:` — wheels only — so no third-party
`setup.py` is ever executed while building. The install happens in `docker build` (network is
needed there); tests later run from the finished image with no network. Images are cached by a
hash of the base image's *content id* (so rebuilding the base image invalidates them) and the
exact requirement list.

The base image is the sandbox image of the Python version the project needs
(sandbox/python_versions.py); the choice and its reason are in the notes. Projects with browser
tests (Playwright) are built on the browser sandbox image instead, which adds a headless
Chromium and has the default Python. Playwright itself comes from that image, pinned to the browser
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

from testmind.analyzers.repository.models import Dependency, DependencyGroup, PythonRequirement
from testmind.core.logging import get_logger
from testmind.sandbox.python_versions import (
    DEFAULT_PYTHON,
    SANDBOX_PYTHONS,
    choose_python,
    image_for,
)

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
    python: str = DEFAULT_PYTHON


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
        python_versions: tuple[str, ...] = (DEFAULT_PYTHON,),
        docker_binary: str = "docker",
        timeout_seconds: int = 900,
    ) -> None:
        self._base = base_image
        self._browser = browser_image
        unknown = [v for v in python_versions if v not in SANDBOX_PYTHONS]
        if unknown:
            raise ValueError(
                f"No sandbox image is defined for Python {', '.join(unknown)} "
                f"(TESTMIND_SANDBOX_PYTHON_VERSIONS; supported: {', '.join(SANDBOX_PYTHONS)})"
            )
        self._pythons = list(python_versions)
        self._docker = docker_binary
        self._timeout = timeout_seconds

    def tag_for(self, requirements: list[str], base_id: str) -> str:
        digest = hashlib.sha256("\n".join([base_id, *requirements]).encode()).hexdigest()
        return f"{IMAGE_REPOSITORY}:{digest[:16]}"

    def ensure(
        self,
        dependencies: Iterable[Dependency],
        *,
        browser: bool = False,
        python: PythonRequirement | None = None,
    ) -> RunnerImage:
        """The runner image for these dependencies, on the sandbox image of the Python version
        the project needs; `browser`: built on the browser image (default Python)."""
        choice = choose_python(python or PythonRequirement(), self._pythons)
        version = choice.version
        base = image_for(self._base, version)
        notes = [f"Python {version}: {choice.reason}"]
        if browser:
            version = DEFAULT_PYTHON
            notes = [f"Python {DEFAULT_PYTHON}: browser tests run on the browser image"]
            if self._browser is None:
                raise DependencyInstallError(
                    "The project has browser tests, but no browser sandbox image is configured "
                    "(TESTMIND_BROWSER_SANDBOX_IMAGE)",
                    "",
                    (),
                )
            base = self._browser
            dependencies = [d for d in dependencies if d.name not in BROWSER_PROVIDED]
        requirements, adjustments = requirement_lines(dependencies)
        notes += adjustments
        if browser:
            notes.append(f"Playwright and a headless Chromium come from {base}")
        if not requirements:
            return RunnerImage(base, (), True, "", tuple(notes), version)
        tag = self.tag_for(requirements, self._base_id(base, notes))
        if self._exists(tag):
            return RunnerImage(tag, tuple(requirements), True, "", tuple(notes), version)

        with tempfile.TemporaryDirectory(prefix="testmind-runner-") as context:
            (Path(context) / "Dockerfile").write_text(DOCKERFILE.format(base=base))
            (Path(context) / "requirements.txt").write_text("\n".join(requirements) + "\n")
            try:
                completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                    [self._docker, "build", "--tag", tag, context],
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
        return RunnerImage(tag, tuple(requirements), False, log, tuple(notes), version)

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
