from pathlib import Path

import pytest

from testmind.analyzers.repository.infrastructure import (
    is_compose_file,
    is_config_file,
    is_dockerfile,
)
from tests.unit.analyzers.helpers import analyze


@pytest.mark.parametrize(
    ("path", "config"),
    [("pyproject.toml", True), ("sub/tox.ini", True), ("requirements-dev.txt", True),
     (".pre-commit-config.yaml", True), ("notes.txt", False), ("main.py", False)],
)  # fmt: skip
def test_is_config_file(path: str, config: bool) -> None:
    assert is_config_file(path) is config


@pytest.mark.parametrize(
    ("path", "expected"),
    [("Dockerfile", True), ("docker/Dockerfile.dev", True), ("api.dockerfile", True),
     ("dockerfile.md", False)],
)  # fmt: skip
def test_is_dockerfile(path: str, expected: bool) -> None:
    assert is_dockerfile(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [("docker-compose.yml", True), ("compose.yaml", True), ("docker-compose.test.yml", True),
     ("compose.override.yml", True), ("composer.json", False), ("my-compose.yml", False)],
)  # fmt: skip
def test_is_compose_file(path: str, expected: bool) -> None:
    assert is_compose_file(path) is expected


def test_dockerfile_base_images(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "Dockerfile": (
                "ARG PY=3.12\nARG DISTRO='bookworm'\n"
                "FROM --platform=linux/amd64 python:${PY}-slim-$DISTRO AS builder\n"
                "FROM builder AS test\n"
                "FROM gcr.io/distroless/python3:$UNKNOWN\n"
                "FROM scratch\n"
            ),
            "docker/Dockerfile.worker": "from node:20 as web\n",
        },
    )
    assert profile.docker.dockerfiles == ["Dockerfile", "docker/Dockerfile.worker"]
    assert profile.docker.base_images == [
        "gcr.io/distroless/python3:$UNKNOWN",
        "node:20",
        "python:3.12-slim-bookworm",
    ]


def test_compose_services_across_files(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            "docker-compose.yml": "services:\n  api: {}\n  db: {}\n",
            "compose.override.yml": "services:\n  api: {}\n  cache: {}\n",
            "docker-compose.empty.yml": "version: '3'\n",
            "compose.list.yml": "- not a mapping\n",
        },
    )
    assert profile.docker.compose_services == ["api", "cache", "db"]
    assert profile.issues == []


def test_invalid_yaml_is_reported(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {"docker-compose.yml": "services: [unclosed\n"})
    assert profile.docker.compose_services == []
    assert profile.issues[0].path == "docker-compose.yml"
    assert profile.issues[0].message.startswith("Invalid YAML")


def test_yaml_is_loaded_safely(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path, {"docker-compose.yml": "services: !!python/object/apply:os.system ['id']\n"}
    )
    assert profile.docker.compose_services == []
    assert profile.issues[0].message.startswith("Invalid YAML")


def test_ci_providers_and_jobs(tmp_path: Path) -> None:
    profile = analyze(
        tmp_path,
        {
            ".github/workflows/ci.yml": "on: push\njobs:\n  test: {}\n  build: {}\n",
            ".github/workflows/README.md": "not a workflow",
            ".gitlab-ci.yml": (
                "stages: [test]\nvariables: {A: 1}\n.template: {script: [x]}\n"
                "unit: {script: [pytest]}\nlint: {script: [ruff]}\ninclude: []\n"
            ),
            ".circleci/config.yml": "jobs:\n  tests: {}\n",
            "Jenkinsfile": "pipeline {}\n",
            ".travis.yml": "language: python\n",
        },
    )
    assert [(c.provider, c.path, c.jobs) for c in profile.ci] == [
        ("circleci", ".circleci/config.yml", ["tests"]),
        ("github_actions", ".github/workflows/ci.yml", ["build", "test"]),
        ("gitlab_ci", ".gitlab-ci.yml", ["lint", "unit"]),
        ("travis_ci", ".travis.yml", []),
        ("jenkins", "Jenkinsfile", []),
    ]


def test_ci_file_without_jobs_table(tmp_path: Path) -> None:
    profile = analyze(tmp_path, {".github/workflows/x.yml": "- 1\n", ".gitlab-ci.yml": "- 1\n"})
    assert [c.jobs for c in profile.ci] == [[], []]
