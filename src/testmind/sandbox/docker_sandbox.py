"""Run pytest for a repository plus extra test files inside a locked-down Docker container.

Isolation (every run):
  --network none, --cap-drop ALL, --security-opt no-new-privileges, --user nobody,
  --read-only root filesystem with a small /tmp tmpfs, CPU / memory / pids limits,
  the repository and the test files mounted read-only, a wall-clock timeout enforced from outside
  (the container is killed), and no environment variables from the host.
Only /out (a fresh temporary directory) is writable, and only the JUnit report (and, when
requested, coverage.py's JSON report and the failure records of TestMind's pytest plugin) is
read back, with a size cap.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - only fixed argument lists, never a shell
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from testmind.core.logging import get_logger
from testmind.sandbox.junit import parse_junit
from testmind.sandbox.models import SandboxResult, SandboxUnavailableError

logger = get_logger(__name__)

MAX_OUTPUT = 20_000
PIDS_LIMIT = 256
DOCKER_CHECK_TIMEOUT = 15
MAX_COVERAGE_BYTES = 50 * 1024 * 1024
MAX_FAILURES_BYTES = 10 * 1024 * 1024
COVERAGE_CONFIG = "/opt/testmind/coveragerc"  # baked into the sandbox image
CONTEXTS_CONFIG = "/opt/testmind/coveragerc_contexts"  # the same, plus per-test contexts
PLUGIN_DIR = "/opt/testmind"  # testmind_failures.py, baked into the sandbox image


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

    @property
    def image(self) -> str:
        return self._image

    def available(self, image: str | None = None) -> bool:
        """Docker CLI present, daemon reachable and the image built."""
        if shutil.which(self._docker) is None:
            return False
        try:
            probe = subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
                [self._docker, "image", "inspect", image or self._image],
                capture_output=True,
                timeout=DOCKER_CHECK_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return probe.returncode == 0

    def run_pytest(
        self, repo_root: Path, test_files: dict[str, str], *, image: str | None = None
    ) -> SandboxResult:
        """Run only the given test files (relative path -> source) against the repository."""
        return self.run(repo_root, PytestJob(test_files=test_files, image=image))

    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult:
        image = job.image or self._image
        if not self.available(image):
            raise SandboxUnavailableError(f"Docker sandbox image '{image}' is not available")
        for relative in job.test_files:
            _check_relative(relative)
        for relative in job.project_tests:
            _check_relative(relative)
        for nodeid in job.select:
            _check_nodeid(nodeid, job.project_tests)
        for key, value in job.env.items():
            _check_env(key, value)

        with tempfile.TemporaryDirectory(prefix="testmind-sandbox-") as scratch:
            tests_dir = Path(scratch) / "tests"
            out_dir = Path(scratch) / "out"
            tests_dir.mkdir()
            out_dir.mkdir()
            out_dir.chmod(0o777)  # written by the unprivileged container user
            for relative, source in job.test_files.items():
                target = tests_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")
            tests_dir.chmod(0o755)

            name = f"testmind-sandbox-{uuid.uuid4().hex[:12]}"
            command = self._command(
                name,
                image,
                repo_root=repo_root.resolve(),
                tests_dir=tests_dir,
                out_dir=out_dir,
                job=job,
            )
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
            coverage_json, coverage_error = (
                _read_coverage(out_dir / "coverage.json") if job.coverage else (None, None)
            )
            failures_json = (
                _read_capped(out_dir / "failures.json", MAX_FAILURES_BYTES)
                if job.capture_failures
                else None
            )

        logger.info(
            "sandbox_finished", exit_code=exit_code, timed_out=timed_out, seconds=round(duration, 2)
        )
        return SandboxResult(
            exit_code=exit_code,
            timed_out=timed_out,
            duration_seconds=round(duration, 3),
            tests=parse_junit(xml),
            output=output[-MAX_OUTPUT:],
            coverage_json=coverage_json,
            coverage_error=coverage_error,
            failures_json=failures_json,
        )

    def _command(
        self,
        name: str,
        image: str,
        *,
        repo_root: Path,
        tests_dir: Path,
        out_dir: Path,
        job: PytestJob,
    ) -> list[str]:
        # The project's own tests run from the repository root with its conftest files;
        # generated tests live in a separate read-only directory.
        # `select` narrows the project's tests to single test files or test nodeids.
        selected = job.select or job.project_tests
        targets = [f"/workspace/src/{relative}" for relative in selected]
        targets += [f"/workspace/tests/{relative}" for relative in sorted(job.test_files)]
        rootdir = "/workspace/src" if job.project_tests else "/workspace/tests"
        env: list[str] = []
        for key, value in sorted(job.env.items()):
            env += ["--env", f"{key}={value}"]
        coverage: list[str] = []
        if job.coverage:
            # TestMind's own coverage config: the project's fail_under / omit do not apply.
            config = CONTEXTS_CONFIG if job.test_contexts else COVERAGE_CONFIG
            coverage = [
                "--cov=/workspace/src",
                f"--cov-config={config}",
                "--cov-report=json:/out/coverage.json",
            ]
            if job.test_contexts:
                coverage.append("--cov-context=test")
        plugin = ["-p", "testmind_failures"] if job.capture_failures else []
        if job.continue_on_collection_errors:
            plugin.append("--continue-on-collection-errors")
        # The plugin directory comes first so that no repository module can shadow it.
        pythonpath = "/workspace/src:/workspace/src/src"
        if job.capture_failures:
            pythonpath = f"{PLUGIN_DIR}:{pythonpath}"
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
            "--workdir", rootdir,
            "--env", f"PYTHONPATH={pythonpath}",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--env", "HOME=/tmp",
            *env,
            "--mount", f"type=bind,source={repo_root},target=/workspace/src,readonly",
            "--mount", f"type=bind,source={tests_dir},target=/workspace/tests,readonly",
            "--mount", f"type=bind,source={out_dir},target=/out",
            image,
            "python", "-m", "pytest",
            "-p", "no:cacheprovider",
            "--rootdir", rootdir,
            # The project's addopts may need plugins or write files (e.g. --cov); coverage is
            # measured separately (Phase 6).
            "-o", "addopts=",
            "-o", "junit_family=xunit1",  # adds file and line to each test case
            "--junitxml", "/out/report.xml",
            *coverage,
            *plugin,
            "-q", *targets,
        ]  # fmt: skip

    def _kill(self, name: str) -> None:
        subprocess.run(  # noqa: S603  # nosec B603 - fixed argv, no shell
            [self._docker, "rm", "--force", name],
            capture_output=True,
            timeout=DOCKER_CHECK_TIMEOUT,
            check=False,
        )


@dataclass(frozen=True, slots=True)
class PytestJob:
    """What to run: generated test files and/or the project's own test files."""

    test_files: dict[str, str] = field(default_factory=dict)  # extra files, relative -> source
    project_tests: tuple[str, ...] = ()  # repository test files, relative to the root
    image: str | None = None  # defaults to the sandbox base image
    env: dict[str, str] = field(default_factory=dict)
    coverage: bool = False  # measure line + branch coverage of the repository
    test_contexts: bool = False  # with coverage: record which test executed each line
    # Run only these project tests: files from `project_tests`, or nodeids within them.
    select: tuple[str, ...] = ()
    capture_failures: bool = False  # structured failure records (testmind_failures plugin)
    # One test module that fails to import must not hide the results of all the others.
    continue_on_collection_errors: bool = False


def _read_capped(path: Path, limit: int) -> str | None:
    if not path.is_file() or path.stat().st_size > limit:
        return None
    return path.read_text("utf-8", errors="replace")


def _read_coverage(path: Path) -> tuple[str | None, str | None]:
    """(report, None) or (None, why there is no report)."""
    if not path.is_file():
        return None, "coverage.py wrote no report (the run timed out or pytest crashed)"
    if path.stat().st_size > MAX_COVERAGE_BYTES:
        return None, f"The coverage report exceeds {MAX_COVERAGE_BYTES // (1024 * 1024)} MB"
    return path.read_text("utf-8", errors="replace"), None


_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_ENV_VALUE = re.compile(r"^[A-Za-z0-9_.:/-]{0,256}$")


def _check_env(key: str, value: str) -> None:
    if not _ENV_KEY.match(key) or not _ENV_VALUE.match(value):
        raise ValueError(f"Invalid sandbox environment variable: {key!r}")


_NODEID_PART = re.compile(r"^[^\x00-\x1f]{1,1000}$")


def _check_nodeid(nodeid: str, project_tests: tuple[str, ...]) -> None:
    """A nodeid becomes a pytest argument: it must name one of the project's test files and
    cannot start an option or contain control characters."""
    file, _, rest = nodeid.partition("::")
    if file not in project_tests or (rest and not _NODEID_PART.match(rest)):
        raise ValueError(f"Invalid test selection: {nodeid!r}")


def _check_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not relative.endswith(".py"):
        raise ValueError(f"Invalid test file path: {relative!r}")
