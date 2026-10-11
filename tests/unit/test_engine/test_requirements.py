from pathlib import Path

import pytest

from testmind.analyzers.repository.models import Dependency, DependencyGroup, PythonRequirement
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
    assert builder.tag_for(["a", "b"], "sha256:1") == builder.tag_for(["a", "b"], "sha256:1")
    assert builder.tag_for(["a"], "sha256:1") != builder.tag_for(["a", "b"], "sha256:1")
    # A rebuilt base image (new content id, same name) must not reuse old runner images.
    assert builder.tag_for(["a"], "sha256:2") != builder.tag_for(["a"], "sha256:1")
    assert builder.tag_for(["a"], "sha256:1").startswith("testmind-runner:")


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


# `docker image inspect --format {{.Id}} base` answers with a content id; other probes fail.
INSPECT_BASE = 'if [ "$3" = "--format" ]; then echo sha256:base; exit 0; fi\n'


def test_base_image_id_is_part_of_the_tag(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    docker = fake_docker(
        tmp_path,
        f'echo "$@" >> {log}\n' + INSPECT_BASE + 'if [ "$1" = "image" ]; then exit 0; fi\n',
    )
    image = RunnerImageBuilder("base", docker_binary=docker).ensure([dep("six")])
    builder = RunnerImageBuilder("base")
    assert image.requirements == ("coverage==7.16.1", "pytest-cov==7.1.0", "pytest==8.4.2", "six")
    assert image.tag == builder.tag_for(list(image.requirements), "sha256:base")
    assert image.from_cache is True
    assert log.read_text().splitlines()[0] == "image inspect --format {{.Id}} base"


def test_the_sandbox_images_pytest_and_coverage_are_kept(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, INSPECT_BASE + 'if [ "$1" = "image" ]; then exit 0; fi\n')
    builder = RunnerImageBuilder("base", python_versions=("3.7",), docker_binary=docker)
    image = builder.ensure(
        [
            dep("pytest", "==3.2.1"),
            dep("pytest_cov"),
            dep("mock"),
            dep("pluggy", "==0.13.1"),
            dep("py", "==1.8.1"),  # kept: old plugins need the real package (py.io)
        ],
        python=PythonRequirement(pinned="3.7"),
    )
    assert image.requirements == (
        "coverage==7.2.7",
        "mock",
        "py==1.8.1",
        "pytest-cov==4.1.0",
        "pytest==7.4.4",
    )
    assert image.notes[1] == (
        "pluggy, pytest, pytest_cov: the sandbox image's versions are used "
        "(coverage 7.2.7, pytest 7.4.4, pytest-cov 4.1.0), not the project's"
    )
    # Nothing else to install: the sandbox image itself, no pins needed.
    bare = builder.ensure([dep("pytest")], python=PythonRequirement(pinned="3.7"))
    assert (bare.tag, bare.requirements) == ("base:py3.7", ())


def test_docker_disappearing_after_the_base_image_probe(tmp_path: Path) -> None:
    # The fake binary removes itself after answering the base-image probe.
    docker = fake_docker(tmp_path, 'if [ "$3" = "--format" ]; then echo sha256:b; rm "$0"; fi\n')
    with pytest.raises(DependencyInstallError, match="Docker is not available"):
        RunnerImageBuilder("base", docker_binary=docker).ensure([dep("six")])


def test_missing_base_image(tmp_path: Path) -> None:
    docker = fake_docker(tmp_path, "echo 'No such image' >&2\nexit 1\n")
    with pytest.raises(
        DependencyInstallError, match="base image 'base' is not available"
    ) as caught:
        RunnerImageBuilder("base", docker_binary=docker).ensure([dep("six")])
    assert "No such image" in caught.value.log


def test_missing_docker_is_an_install_error(tmp_path: Path) -> None:
    builder = RunnerImageBuilder("base", docker_binary=str(tmp_path / "no-docker"))
    with pytest.raises(DependencyInstallError, match="Docker is not available"):
        builder.ensure([dep("six")])


def test_build_timeout(tmp_path: Path) -> None:
    docker = fake_docker(
        tmp_path, INSPECT_BASE + 'if [ "$1" = "image" ]; then exit 1; fi\nsleep 5\n'
    )
    builder = RunnerImageBuilder("base", docker_binary=docker, timeout_seconds=1)
    with pytest.raises(DependencyInstallError, match="timed out"):
        builder.ensure([dep("six")])


def test_broken_docker_binary_when_probing(tmp_path: Path) -> None:
    broken = tmp_path / "docker"
    broken.write_text("not an executable format")
    broken.chmod(0o755)
    with pytest.raises(DependencyInstallError, match="Docker is not available"):
        RunnerImageBuilder("base", docker_binary=str(broken)).ensure([dep("six")])


def test_the_projects_python_chooses_the_base_image(tmp_path: Path) -> None:
    log = tmp_path / "calls"
    docker = fake_docker(
        tmp_path,
        f'echo "$@" >> {log}\n' + INSPECT_BASE + 'if [ "$1" = "image" ]; then exit 0; fi\n',
    )
    builder = RunnerImageBuilder(
        "testmind-sandbox:local", python_versions=("3.8", "3.12"), docker_binary=docker
    )
    image = builder.ensure([dep("six")], python=PythonRequirement(tested=["3.6", "3.8"]))
    assert image.python == "3.8"
    assert image.notes[0] == "Python 3.8: newest version the project is tested on (3.6, 3.8)"
    assert "--format {{.Id}} testmind-sandbox:py3.8" in log.read_text()
    bare = builder.ensure([], python=PythonRequirement(pinned="3.8"))
    assert (bare.tag, bare.python, bare.notes) == (
        "testmind-sandbox:py3.8", "3.8", ("Python 3.8: pinned to 3.8",),
    )  # fmt: skip
    default = RunnerImageBuilder("testmind-sandbox:local").ensure([])
    assert (default.tag, default.python) == ("testmind-sandbox:local", "3.12")
    assert default.notes == (
        "Python 3.12: the project declares no Python version; newest available",
    )


def test_browser_projects_run_on_the_browser_image_and_its_python() -> None:
    builder = RunnerImageBuilder("base:1", browser_image="browser:1", python_versions=("3.8",))
    image = builder.ensure([], browser=True, python=PythonRequirement(pinned="3.8"))
    assert (image.tag, image.python) == ("browser:1", "3.12")
    assert image.notes[0] == "Python 3.12: browser tests run on the browser image"
    plain = builder.ensure([], browser=True)
    assert plain.notes[0] == "Python 3.12: browser tests run on the browser image"


def test_versions_without_a_sandbox_definition_are_refused() -> None:
    with pytest.raises(ValueError, match=r"No sandbox image is defined for Python 3\.6"):
        RunnerImageBuilder("base:1", python_versions=("3.6", "3.8"))
