"""Which recorded pins have a wheel the sandbox can install (PyPI's JSON API, faked)."""

from __future__ import annotations

from pathlib import Path

import httpx

from benchmarks.bugsinpy.wheels import WheelIndex, sandbox_tags

FILES = {
    # Windows wheels only, like psutil 5.7.0
    "/pypi/psutil/5.7.0/json": ["psutil-5.7.0-cp37-cp37m-win_amd64.whl", "psutil-5.7.0.tar.gz"],
    "/pypi/six/1.15.0/json": ["six-1.15.0-py2.py3-none-any.whl"],
    "/pypi/cffi/1.14.0/json": [
        "not-a-wheel-name.whl",
        "cffi-1.14.0-cp37-cp37m-manylinux1_x86_64.whl",
    ],
}


def pypi(requests: list[str]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        files = FILES.get(request.url.path)
        if files is None:
            return httpx.Response(404)
        kinds = [("sdist" if f.endswith(".tar.gz") else "bdist_wheel", f) for f in files]
        urls = [{"packagetype": kind, "filename": name} for kind, name in kinds]
        return httpx.Response(200, json={"urls": urls})

    return httpx.Client(base_url="https://pypi.org", transport=httpx.MockTransport(handle))


def test_only_wheels_for_cpython_on_linux_count(tmp_path: Path) -> None:
    requests: list[str] = []
    index = WheelIndex(tmp_path / "wheels.json", pypi(requests))
    assert index.has_wheel("psutil", "5.7.0", "3.7") is False
    assert index.has_wheel("six", "1.15.0", "3.12") is True
    assert index.has_wheel("cffi", "1.14.0", "3.7") is True
    assert index.has_wheel("cffi", "1.14.0", "3.8") is False  # a cp37 wheel only
    assert index.has_wheel("gone", "1.0", "3.8") is False  # not on PyPI
    # Answers are cached on disk and reused by a new index.
    again = WheelIndex(tmp_path / "wheels.json", pypi(requests))
    assert again.has_wheel("Six", "1.15.0", "3.12") is True
    assert len(requests) == 5


def test_sandbox_tags() -> None:
    tags = {str(tag) for tag in sandbox_tags("3.7")}
    assert "cp37-cp37m-manylinux_2_17_x86_64" in tags
    assert "py3-none-any" in tags
    assert "cp38-cp38-manylinux_2_17_x86_64" not in {str(t) for t in sandbox_tags("3.7")}
    assert "cp38-cp38-manylinux1_x86_64" in {str(t) for t in sandbox_tags("3.8")}
