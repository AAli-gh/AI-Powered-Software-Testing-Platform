"""Run pytest for a repository plus extra test files inside a locked-down Docker container.

Isolation (every run):
  --network none, --cap-drop ALL, --security-opt no-new-privileges, --user nobody,
  --read-only root filesystem with a small /tmp tmpfs, CPU / memory / pids limits,
  the repository and the test files mounted read-only, a wall-clock timeout enforced from outside
  (the container is killed), and no environment variables from the host.
Only /out (a fresh temporary directory) is writable, and only the JUnit report is read back.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - only fixed argument lists, never a shell
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from testmind.core.logging import get_logger
from testmind.sandbox.junit import parse_junit
from testmind.sandbox.models import SandboxResult, SandboxUnavailableError

logger = get_logger(__name__)

MAX_OUTPUT = 20_000
PIDS_LIMIT = 256
DOCKER_CHECK_TIMEOUT = 15


class DockerSandbox:
    def __init__(
        self,
        image: str,
        *,
        timeout_seconds: int,
        memory: str = "512m",
        cpus: float = 1.0,
        docker_binary: str = "docker",
    ) -> None:
        self._image = image
        self._timeout = timeout_seconds
        self._memory = memory
        self._cpus = cpus
        self._docker = docker_binary

    def available(self) -> bool:
        """Docker CLI present, daemon reachable and the sandbox image built."""
        if shutil.which(self._docker) is None:
            return False
        try:
            probe = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                [self._docker, "image", "inspect", self._image],
                capture_output=True,
                timeout=DOCKER_CHECK_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0

    def run_pytest(self, repo_root: Path, test_files: dict[str, str]) -> SandboxResult:
        """Run pytest on `test_files` (relative path -> source) against the repository."""
        if not self.available():
            raise SandboxUnavailableError(f"Docker sandbox image '{self._image}' is not available")
        for relative in test_files:
            _check_relative(relative)

        with tempfile.TemporaryDirectory(prefix="testmind-sandbox-") as scratch:
            tests_dir = Path(scratch) / "tests"
            out_dir = Path(scratch) / "out"
            out_dir.mkdir()
            out_dir.chmod(0o777)  # written by the unprivileged container user
            for relative, source in test_files.items():
                target = tests_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            tests_dir.chmod(0o755)

            name = f"testmind-sandbox-{uuid.uuid4().hex[:12]}"
            command = self._command(name, repo_root.resolve(), tests_dir, out_dir, test_files)
            started = time.monotonic()
            timed_out = False
            try:
                completed = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                    command, capture_output=True, timeout=self._timeout, check=False
                )
                exit_code = completed.returncode
                output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                exit_code = -1
                output = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", "replace")
                self._kill(name)
            duration = time.monotonic() - started

            report = out_dir / "report.xml"
            xml = report.read_text("utf-8", errors="replace") if report.is_file() else ""

        logger.info(
            "sandbox_finished", exit_code=exit_code, timed_out=timed_out, seconds=round(duration, 2)
        )
        return SandboxResult(
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=round(duration, 3),
            tests=parse_junit(xml),
            output=output[-MAX_OUTPUT:],
        )

    def _command(
        self,
        name: str,
        repo_root: Path,
        tests_dir: Path,
        out_dir: Path,
        test_files: dict[str, str],
    ) -> list[str]:
        targets = [f"/workspace/tests/{relative}" for relative in sorted(test_files)]
        return [
            self._docker, "run", "--rm", "--name", name,
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108  # nosec B108 - container tmpfs
            "--memory", self._memory,
            "--memory-swap", self._memory,
            "--cpus", str(self._cpus),
            "--pids-limit", str(PIDS_LIMIT),
            "--env", "PYTHONPATH=/workspace/src:/workspace/src/src",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "HOME=/tmp",
            "--mount", f"type=bind,source={repo_root},target=/workspace/src,readonly",
            "--mount", f"type=bind,source={tests_dir},target=/workspace/tests,readonly",
            "--mount", f"type=bind,source={out_dir},target=/out",
            self._image,
            "python", "-m", "pytest",
            "-p", "no:cacheprovider",
            "--rootdir", "/workspace/tests",
            "-o", "addopts=",
            "--junitxml", "/out/report.xml",
            "-q", *targets,
        ]  # fmt: skip

    def _kill(self, name: str) -> None:
        subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
            [self._docker, "rm", "--force", name],
            capture_output=True,
            timeout=DOCKER_CHECK_TIMEOUT,
            check=False,
        )


def _check_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not relative.endswith(".py"):
        raise ValueError(f"Invalid test file path: {relative!r}")
