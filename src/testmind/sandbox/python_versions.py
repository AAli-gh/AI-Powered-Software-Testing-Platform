"""The Python versions sandbox images exist for, and which one a project runs on.

Every sandbox image is docker/sandbox.Dockerfile built for one Python version, with the newest
pytest, coverage.py and pytest-cov that support it (`SANDBOX_PYTHONS`, the one place these
pins live; `make sandbox-images` builds them). The default version's image is
TESTMIND_SANDBOX_IMAGE; the others are tagged `<its repository>:py<version>`.

Which version a project runs on is decided from what its own files declare
(`PythonRequirement`, analyzers/repository/python_version.py), among the versions whose
images are configured (TESTMIND_SANDBOX_PYTHON_VERSIONS):

1. a pinned version (`.python-version`, `runtime.txt`), if available;
2. otherwise the versions allowed by the declared range (`requires-python`);
3. among those, the newest the project says it is tested on (tox, classifiers);
4. otherwise the newest allowed.

When nothing available satisfies the project, it runs on the default version, and the notes
say so: its tests may then fail for reasons that are not theirs.
"""

from __future__ import annotations

from dataclasses import dataclass

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from testmind.analyzers.repository.models import PythonRequirement


@dataclass(frozen=True, slots=True)
class SandboxPython:
    version: str
    pytest: str
    coverage: str
    pytest_cov: str


SANDBOX_PYTHONS: dict[str, SandboxPython] = {
    p.version: p
    for p in (
        # Python 3.7 is the oldest: the API capture plugin needs contextvars.
        SandboxPython("3.7", pytest="7.4.4", coverage="7.2.7", pytest_cov="4.1.0"),
        SandboxPython("3.8", pytest="8.3.5", coverage="7.6.1", pytest_cov="5.0.0"),
        SandboxPython("3.9", pytest="8.4.2", coverage="7.10.7", pytest_cov="7.1.0"),
        SandboxPython("3.10", pytest="8.4.2", coverage="7.16.1", pytest_cov="7.1.0"),
        SandboxPython("3.11", pytest="8.4.2", coverage="7.16.1", pytest_cov="7.1.0"),
        SandboxPython("3.12", pytest="8.4.2", coverage="7.16.1", pytest_cov="7.1.0"),
    )
}
DEFAULT_PYTHON = "3.12"


def image_for(default_image: str, version: str) -> str:
    """`testmind-sandbox:local` for the default version, `testmind-sandbox:py3.8` for 3.8."""
    if version == DEFAULT_PYTHON:
        return default_image
    path, _, name = default_image.rpartition("/")
    repository = name.split(":", 1)[0]  # a registry port (host:5000/...) stays in `path`
    return f"{path}/{repository}:py{version}" if path else f"{repository}:py{version}"


@dataclass(frozen=True, slots=True)
class PythonChoice:
    version: str
    reason: str


def choose_python(requirement: PythonRequirement, available: list[str]) -> PythonChoice:
    """The version a project runs on, and why (see the module docstring)."""
    candidates = sorted(
        {DEFAULT_PYTHON, *(v for v in available if v in SANDBOX_PYTHONS)}, key=Version
    )
    if requirement.pinned:
        if requirement.pinned in candidates:
            return PythonChoice(requirement.pinned, f"pinned to {requirement.pinned}")
        unavailable = f"pinned to {requirement.pinned}, which has no sandbox image"
    else:
        unavailable = ""
    allowed = candidates
    if requirement.requires:
        allowed = [v for v in candidates if SpecifierSet(requirement.requires).contains(v)]
    tested = [v for v in allowed if v in requirement.tested]
    if tested:
        reason = f"newest version the project is tested on ({', '.join(requirement.tested)})"
        return PythonChoice(tested[-1], _join(unavailable, reason))
    if allowed:
        if requirement.requires:
            reason = f"newest version allowed by requires-python {requirement.requires}"
        elif requirement.tested:
            reason = (
                f"the project is tested on {', '.join(requirement.tested)}, none of which has "
                "a sandbox image; newest available"
            )
        else:
            reason = "the project declares no Python version; newest available"
        return PythonChoice(allowed[-1], _join(unavailable, reason))
    return PythonChoice(
        DEFAULT_PYTHON,
        _join(
            unavailable,
            f"no sandbox image satisfies requires-python {requirement.requires}; "
            f"running on {DEFAULT_PYTHON}, where tests may fail for that reason",
        ),
    )


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)
