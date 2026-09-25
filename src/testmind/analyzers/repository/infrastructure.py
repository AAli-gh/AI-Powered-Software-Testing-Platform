"""Configuration files, Docker and CI/CD pipelines."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Any

import yaml

from testmind.analyzers.repository.models import CIConfig, DockerInfo, Issue

ReadFile = Callable[[str], str | None]

CONFIG_FILENAMES = frozenset(
    {
        "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "noxfile.py", "pytest.ini",
        "conftest.py", ".coveragerc", "mypy.ini", ".flake8", "ruff.toml", ".ruff.toml",
        ".pylintrc", ".pre-commit-config.yaml", ".editorconfig", ".env.example", "alembic.ini",
        "Makefile", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "requirements.txt",
        "package.json", "tsconfig.json", ".dockerignore", ".gitignore", "manage.py",
    }
)  # fmt: skip

GITLAB_RESERVED = frozenset(
    {
        "stages", "variables", "default", "include", "workflow", "image", "services", "cache",
        "before_script", "after_script", "types", "pages",
    }
)  # fmt: skip

_FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)
_ARG = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", re.IGNORECASE)
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _substitute(value: str, args: dict[str, str]) -> str:
    """Resolve `$NAME` / `${NAME}` from ARG defaults; unknown variables are left as written."""

    def replace(match: re.Match[str]) -> str:
        return args.get(match.group(1) or match.group(2), match.group(0))

    return _VARIABLE.sub(replace, value)


def is_config_file(path: str) -> bool:
    name = PurePosixPath(path).name
    if name in CONFIG_FILENAMES:
        return True
    return name.startswith("requirements") and name.endswith(".txt")


def is_dockerfile(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("Dockerfile") or name.endswith(".dockerfile")


def is_compose_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return bool(re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", name))


def analyse_docker(paths: list[str], read: ReadFile, issues: list[Issue]) -> DockerInfo:
    dockerfiles = sorted(p for p in paths if is_dockerfile(p))
    compose_files = sorted(p for p in paths if is_compose_file(p))

    base_images: set[str] = set()
    for path in dockerfiles:
        stages: set[str] = set()
        args: dict[str, str] = {}
        for line in (read(path) or "").splitlines():
            arg = _ARG.match(line)
            if arg:
                args.setdefault(arg.group(1), arg.group(2).strip("\"'"))
            match = _FROM.match(line)
            if match:
                image, alias = _substitute(match.group(1), args), match.group(2)
                if image.lower() not in stages and image.lower() != "scratch":
                    base_images.add(image)
                if alias:
                    stages.add(alias.lower())

    services: set[str] = set()
    for path in compose_files:
        data = _load_yaml(path, read, issues)
        compose_services = data.get("services") if isinstance(data, dict) else None
        if isinstance(compose_services, dict):
            services.update(str(name) for name in compose_services)

    return DockerInfo(
        dockerfiles=dockerfiles,
        base_images=sorted(base_images),
        compose_files=compose_files,
        compose_services=sorted(services),
    )


def analyse_ci(paths: list[str], read: ReadFile, issues: list[Issue]) -> list[CIConfig]:
    found: list[CIConfig] = []
    for path in sorted(paths):
        pure = PurePosixPath(path)
        provider = _ci_provider(pure)
        if provider is None:
            continue
        jobs: list[str] = []
        if provider in {"github_actions", "circleci"}:
            data = _load_yaml(path, read, issues)
            job_table = data.get("jobs") if isinstance(data, dict) else None
            if isinstance(job_table, dict):
                jobs = sorted(str(name) for name in job_table)
        elif provider == "gitlab_ci":
            data = _load_yaml(path, read, issues)
            if isinstance(data, dict):
                jobs = sorted(
                    str(key)
                    for key, value in data.items()
                    if isinstance(value, dict)
                    and str(key) not in GITLAB_RESERVED
                    and not str(key).startswith(".")
                )
        found.append(CIConfig(provider=provider, path=path, jobs=jobs))
    return found


def _ci_provider(path: PurePosixPath) -> str | None:
    posix = path.as_posix()
    if posix.startswith(".github/workflows/") and path.suffix in {".yml", ".yaml"}:
        return "github_actions"
    return {
        ".gitlab-ci.yml": "gitlab_ci",
        ".circleci/config.yml": "circleci",
        "Jenkinsfile": "jenkins",
        "azure-pipelines.yml": "azure_pipelines",
        ".travis.yml": "travis_ci",
        "bitbucket-pipelines.yml": "bitbucket_pipelines",
    }.get(posix)


def _load_yaml(path: str, read: ReadFile, issues: list[Issue]) -> Any:
    text = read(path)
    if text is None:
        return None
    try:
        return yaml.safe_load(text)  # safe_load: never constructs arbitrary Python objects
    except yaml.YAMLError as exc:
        issues.append(Issue(path=path, message=f"Invalid YAML: {exc}".splitlines()[0]))
        return None
