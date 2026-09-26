"""Reading coverage.py's report back from the sandbox's /out directory."""

from pathlib import Path

import pytest

from testmind.sandbox import docker_sandbox
from testmind.sandbox.docker_sandbox import _read_coverage


def test_report_is_read(tmp_path: Path) -> None:
    (tmp_path / "coverage.json").write_text('{"files": {}}')
    assert _read_coverage(tmp_path / "coverage.json") == ('{"files": {}}', None)


def test_missing_report(tmp_path: Path) -> None:
    assert _read_coverage(tmp_path / "coverage.json") == (
        None,
        "coverage.py wrote no report (the run timed out or pytest crashed)",
    )


def test_oversized_report_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file is written next to untrusted code, so its size is capped before reading."""
    monkeypatch.setattr(docker_sandbox, "MAX_COVERAGE_BYTES", 2 * 1024 * 1024)
    (tmp_path / "coverage.json").write_bytes(b" " * (2 * 1024 * 1024 + 1))
    assert _read_coverage(tmp_path / "coverage.json") == (
        None,
        "The coverage report exceeds 2 MB",
    )
