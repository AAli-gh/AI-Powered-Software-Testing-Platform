"""Mutation-testing driver baked into the TestMind sandbox image (not part of the testmind
package). It runs inside the same locked-down container as every test run: no network,
unprivileged, resource limits.

    python /opt/testmind/testmind_mutate.py <job.json> <result.json>

The repository is copied once into the writable tmpfs. Then, for each mutant, the driver:
1. checks that the bytes to replace are the expected original text;
2. writes the mutated file;
3. runs only the tests that executed the mutated line, stopping at the first failure;
4. restores the original file.

Each mutant has its own timeout, after which its whole process group is killed.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SOURCE = Path("/workspace/src")
WORK = Path("/tmp/testmind-mutation")  # noqa: S108 - the container's private tmpfs
MAX_OUTPUT = 4000
EXIT_TESTS_FAILED, EXIT_INTERRUPTED, EXIT_USAGE_ERROR = 1, 2, 4


def main(job_path: str, result_path: str) -> None:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    shutil.copytree(SOURCE, WORK, symlinks=True)
    # Extra test modules (e.g. a candidate test written to kill a mutant), validated by TestMind.
    for relative, source in job.get("files", {}).items():
        target = WORK / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": f"{WORK}:{WORK}/src",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    results = [_run(mutant, env) for mutant in job["mutants"]]
    Path(result_path).write_text(json.dumps({"version": 1, "results": results}), "utf-8")


def _run(mutant: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    target = WORK / mutant["path"]
    original = target.read_bytes()
    start, end = mutant["start"], mutant["end"]
    if original[start:end].decode("utf-8", "replace") != mutant["original"]:
        return _result(mutant, "error", detail="the source does not match the mutant")
    target.write_bytes(original[:start] + mutant["replacement"].encode() + original[end:])
    began = time.monotonic()
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable, "-m", "pytest", "-x", "-q", "-rfE", "-p", "no:cacheprovider",
                "-o", "addopts=", "--rootdir", str(WORK),
                *[str(WORK / test) for test in mutant["tests"]],
            ],
            cwd=WORK,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # a timeout kills pytest and everything it started
        )  # fmt: skip
        try:
            output, _ = process.communicate(timeout=mutant["timeout"])
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return _result(mutant, "timeout", duration=time.monotonic() - began)
    finally:
        target.write_bytes(original)
    text = output.decode("utf-8", "replace")
    duration = time.monotonic() - began
    status = _status(process.returncode, text)
    return _result(
        mutant,
        status,
        duration=duration,
        exit_code=process.returncode,
        killed_by=_first_failure(text) if status == "killed" else None,
        detail=text[-MAX_OUTPUT:] if status == "error" else None,
    )


def _status(exit_code: int, output: str) -> str:
    """0: every test passed, the mutant survived. 1: a test failed. 2: interrupted by a
    collection error. 4: pytest could not find the requested nodeids, which happens when the
    mutant breaks importing their module; only with a collection error is that a detection.
    Anything else is not a verdict on the mutant."""
    if exit_code == 0:
        return "survived"
    if exit_code in {EXIT_TESTS_FAILED, EXIT_INTERRUPTED}:
        return "killed"
    if exit_code == EXIT_USAGE_ERROR and "ERROR collecting " in output:
        return "killed"
    return "error"


def _first_failure(output: str) -> str | None:
    """From pytest's short summary: `FAILED tests/test_x.py::test_a - AssertionError`."""
    for line in output.splitlines():
        for prefix in ("FAILED ", "ERROR "):
            if line.startswith(prefix):
                return line[len(prefix) :].split(" - ", 1)[0].strip()
    return None


def _result(
    mutant: dict[str, Any],
    status: str,
    *,
    duration: float = 0.0,
    exit_code: int | None = None,
    killed_by: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "id": mutant["id"],
        "status": status,
        "duration": round(duration, 3),
        "exit_code": exit_code,
        "killed_by": killed_by,
        "detail": detail,
    }


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
