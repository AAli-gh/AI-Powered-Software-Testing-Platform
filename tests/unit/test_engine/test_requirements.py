from pathlib import Path

import pytest

from testmind.analyzers.repository.models import Dependency, DependencyGroup
from testmind.sandbox.runner_image import (
    DOCKERFILE,
    DependencyInstallError,
    RunnerImageBuilder,
    requirement_lines,
)


def dep(
    name: str, specifier: str = "", group: DependencyGroup = DependencyGroup.RUNTIME, **kw: object
) -> Dependency:
    return Dependency(name=name, specifier=specifier, group=group, source="x", **kw)  # type: ignore[arg-type]


def test_runtime_and_development_are_installed_optional_is_not() -> None:
    lines, notes = requirement_lines(
        [
            dep("django", ">=5"),
            dep("pytest", ">=8", DependencyGroup.DEVELOPMENT),
            dep("asyncpg", ">=0.29", DependencyGroup.OPTIONAL),
            dep("django", ">=5"),  # declared twice (two manifests)
        ]
    )
    assert lines == ["django>=5", "pytest>=8"]
    assert notes == []


def test_extras_and_markers_are_kept() -> None:
    lines, _ = requirement_lines(
        [
            dep("uvicorn", ">=0.29", extras=["standard"]),
            dep("psycopg", marker='sys_platform != "win32"'),
        ]
    )
    assert lines == ['psycopg ; sys_platform != "win32"', "uvicorn[standard]>=0.29"]


def test_non_pep440_specifiers_are_dropped_with_a_note() -> None:
    lines, notes = requirement_lines([dep("flask", "^3.0")])
    assert lines == ["flask"]
    assert notes == ["flask: specifier '^3.0' is not PEP 440, installing the latest wheel instead"]


def test_tag_is_deterministic_and_depends_on_base_and_requirements() -> None:
    builder = RunnerImageBuilder("base:1")
    assert builder.tag_for(["a", "b"]) == builder.tag_for(["a", "b"])
    assert builder.tag_for(["a"]) != builder.tag_for(["a", "b"])
    assert RunnerImageBuilder("base:2").tag_for(["a"]) != builder.tag_for(["a"])
    assert builder.tag_for(["a"]).startswith("testmind-runner:")


def test_only_wheels_are_installed_and_the_image_drops_root() -> None:
    assert "--only-binary=:all:" in DOCKERFILE
    assert DOCKERFILE.rstrip().splitlines()[-2] == "USER 65534:65534"


def test_no_dependencies_means_the_base_image() -> None:
    image = RunnerImageBuilder("base:1").ensure([])
    assert (image.tag, image.requirements, image.from_cache) == ("base:1", (), True)


def fake_docker(tmp_path: Path, script: str) -> str:
    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\n" + script)
    binary.chmod(0o755)
    return str(binary)


def test_missing_docker_is_an_install_error(tmp_path: Path) -> None:
    builder = RunnerImageBuilder("base", docker_binary=str(tmp_path / "no-docker"))
    with pytest.raises(DependencyInstallError, match="Docker is not available"):
        builder.ensure([dep("six")])


def test_build_timeout(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, 'if [ "$1" = "image" ]; then exit 1; fi\nsleep 5\n')
    builder = RunnerImageBuilder("base", docker_binary=docker, timeout_seconds=1)
    with pytest.raises(DependencyInstallError, match="timed out"):
        builder.ensure([dep("six")])


def test_broken_docker_binary_when_probing(tmp_path: Path) -> None:
    broken = tmp_path / "docker"
    broken.write_text("not an executable format")
    broken.chmod(0o755)
    with pytest.raises(DependencyInstallError, match="Docker is not available"):
        RunnerImageBuilder("base", docker_binary=str(broken)).ensure([dep("six")])
