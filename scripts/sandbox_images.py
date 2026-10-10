"""Build the sandbox image of every Python version TestMind supports (make sandbox-images).

    uv run python scripts/sandbox_images.py [--image testmind-sandbox:local] [--versions 3.8,3.12]
        [-- <extra docker build arguments>]

Versions and their pytest / coverage.py / pytest-cov pins come from SANDBOX_PYTHONS
(src/testmind/sandbox/python_versions.py). The default version is tagged with --image, the
others `<repository>:py<version>`, the names the image builder looks for. Prints the
TESTMIND_SANDBOX_PYTHON_VERSIONS value to configure.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 - fixed argument lists, never a shell
import sys
from pathlib import Path

from testmind.sandbox.python_versions import SANDBOX_PYTHONS, image_for

ROOT = Path(__file__).resolve().parent.parent


def build_commands(image: str, versions: list[str], extra: list[str]) -> list[list[str]]:
    commands = []
    for version in versions:
        pins = SANDBOX_PYTHONS[version]
        commands.append([
            "docker", "build", "-f", "docker/sandbox.Dockerfile",
            "--build-arg", f"PYTHON_VERSION={version}",
            "--build-arg", f"PYTEST_VERSION={pins.pytest}",
            "--build-arg", f"COVERAGE_VERSION={pins.coverage}",
            "--build-arg", f"PYTEST_COV_VERSION={pins.pytest_cov}",
            "-t", image_for(image, version), *extra, ".",
        ])  # fmt: skip
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--image", default="testmind-sandbox:local")
    parser.add_argument("--versions", default=",".join(SANDBOX_PYTHONS))
    parser.add_argument("extra", nargs="*", help="arguments for docker build (after --)")
    args = parser.parse_args(argv)
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    unknown = [v for v in versions if v not in SANDBOX_PYTHONS]
    if unknown:
        parser.error(f"unsupported Python versions: {', '.join(unknown)}")
    for command in build_commands(args.image, versions, args.extra):
        print("+", " ".join(command), flush=True)  # noqa: T201
        subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603  # nosec B603
    print(f"TESTMIND_SANDBOX_PYTHON_VERSIONS={','.join(versions)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
