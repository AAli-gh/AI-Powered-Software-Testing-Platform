"""Whether PyPI has a wheel of a pinned package for the sandbox (CPython, Linux x86_64).

TestMind installs wheels only, so a recorded pin without one (psutil 5.7.0 had Windows wheels
only) would fail the whole installation. The recorded environment keeps a pin only when a
compatible wheel exists; without it the project's own declaration applies. Answers are cached
in a JSON file, since the same pins recur across a project's bugs.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename

# The sandbox images are Debian bookworm (glibc 2.36), x86_64.
PLATFORMS = [
    *(f"manylinux_2_{minor}_x86_64" for minor in range(36, 4, -1)),
    "manylinux2014_x86_64", "manylinux2010_x86_64", "manylinux1_x86_64", "linux_x86_64",
]  # fmt: skip


def sandbox_tags(python: str) -> frozenset[Tag]:
    major, minor = (int(part) for part in python.split(".")[:2])
    abis = [
        f"cp{major}{minor}m" if (major, minor) < (3, 8) else f"cp{major}{minor}",
        "abi3",
        "none",
    ]
    version = (major, minor)
    return frozenset(
        [
            *cpython_tags(version, abis=abis, platforms=PLATFORMS),
            *compatible_tags(version, platforms=PLATFORMS),
        ]
    )


class WheelIndex:
    def __init__(self, cache: Path, client: httpx.Client | None = None) -> None:
        self._path = cache
        self._http = client or httpx.Client(base_url="https://pypi.org", timeout=60)
        self._known: dict[str, bool] = json.loads(cache.read_text()) if cache.is_file() else {}

    def has_wheel(self, name: str, version: str, python: str) -> bool:
        key = f"{canonicalize_name(name)}=={version}@{python}"
        if key not in self._known:
            self._known[key] = self._lookup(name, version, python)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._known, indent=0, sort_keys=True))
        return self._known[key]

    def _lookup(self, name: str, version: str, python: str) -> bool:
        response = self._http.get(f"/pypi/{name}/{version}/json")
        if response.status_code == httpx.codes.NOT_FOUND:
            return False
        tags = sandbox_tags(python)
        for file in response.raise_for_status().json()["urls"]:
            if file["packagetype"] != "bdist_wheel":
                continue
            try:
                _, _, _, wheel_tags = parse_wheel_filename(file["filename"])
            except InvalidWheelFilename:
                continue
            if wheel_tags & tags:
                return True
        return False
