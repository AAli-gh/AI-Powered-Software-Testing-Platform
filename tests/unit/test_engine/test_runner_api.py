"""API coverage and probes in the runner (canned sandbox; the real one is in
tests/sandbox/test_api_testing.py)."""

import json
from pathlib import Path

from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.analyzers.repository import RepositoryProfile
from testmind.api_engine.models import ProbeVerdict
from testmind.api_engine.probes import PROBE_FILE
from testmind.sandbox.models import Outcome
from testmind.test_engine.runner import TestSuiteRunner
from tests.unit.analyzers.helpers import analyze
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

APP = (
    "from fastapi import FastAPI\n\napp = FastAPI()\n\n\n"
    "@app.get('/items/{item_id}')\ndef item(item_id: int):\n    return {}\n"
)


def project(tmp_path: Path, files: dict[str, str]) -> tuple[RepositoryProfile, CodeProfile]:
    repository = analyze(tmp_path, {"tests/test_a.py": "def test_a(): pass\n", **files})
    python = [f.path for f in repository.files if f.language == "Python"]
    return repository, CodeAnalyzer().analyze(tmp_path, python, repository.test_files)


def capture(*calls: dict[str, object]) -> str:
    return json.dumps(
        {
            "version": 1,
            "rootdir": "/workspace/src",
            "calls": list(calls),
            "apps": [
                {
                    "framework": "fastapi",
                    "routes": [{"path": "/items/{item_id}", "methods": ["GET"]}],
                }
            ],
        }
    )


def test_api_coverage_and_probes(tmp_path: Path) -> None:
    repository, code = project(tmp_path, {"main.py": APP})
    request = {
        "test_path": "/workspace/src/tests/test_a.py", "test_name": "test_a",
        "framework": "fastapi", "method": "GET", "route": "/items/{item_id}",
        "path": "/items/1", "status": 200,
    }  # fmt: skip
    probe = {
        **request, "test_path": f"/workspace/tests/{PROBE_FILE}",
        "test_name": "test_probe_001_unknown_path", "route": None, "status": 404,
    }  # fmt: skip
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), api_json=capture(request, probe)))
    suite = TestSuiteRunner(sandbox, Images()).run(tmp_path, repository, code=code, api_probes=True)

    [job] = sandbox.jobs
    assert job.capture_api
    assert PROBE_FILE in job.test_files  # the probe module runs with the suite
    assert "from main import app as _source_0" in job.test_files[PROBE_FILE]
    assert suite.api is not None
    assert (suite.api.totals.tested, suite.api.totals.percent) == (1, 100.0)
    verdicts = [(p.test, p.verdict) for p in suite.api.probes]
    assert verdicts[0] == ("test_probe_001_unknown_path", ProbeVerdict.PASSED)
    assert {v for _, v in verdicts[1:]} == {ProbeVerdict.NOT_RUN}


def test_without_probes_no_module_is_added(tmp_path: Path) -> None:
    repository, code = project(tmp_path, {"main.py": APP})
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), api_json=capture()))
    suite = TestSuiteRunner(sandbox, Images()).run(tmp_path, repository, code=code)
    assert sandbox.jobs[0].test_files == {}
    assert suite.api is not None
    assert suite.api.probes == []


def test_capture_problems_become_notes(tmp_path: Path) -> None:
    repository, code = project(tmp_path, {"main.py": APP})
    runner = TestSuiteRunner(Sandbox(result(case("test_a", Outcome.PASSED))), Images())
    missing = runner.run(tmp_path, repository, code=code).api
    assert missing is not None
    assert missing.notes[0] == (
        "The API capture plugin wrote no records (the run timed out or crashed)"
    )
    broken = (
        TestSuiteRunner(Sandbox(result(case("test_a", Outcome.PASSED), api_json="{}")), Images())
        .run(tmp_path, repository, code=code)
        .api
    )
    assert broken is not None
    assert broken.notes[0].startswith("Not a TestMind API capture")


def test_no_endpoints_and_no_requests_means_no_report(tmp_path: Path) -> None:
    repository, code = project(tmp_path, {"lib.py": "def f():\n    return 1\n"})
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), api_json=json.dumps(
        {"version": 1, "rootdir": "/workspace/src", "calls": [], "apps": []}
    )))  # fmt: skip
    runner = TestSuiteRunner(sandbox, Images())
    assert runner.run(tmp_path, repository, code=code).api is None
    probes = runner.run(tmp_path, repository, code=code, api_probes=True)
    assert probes.api is None  # nothing to report, although probes were asked for


def test_probes_requested_without_a_probe_able_application(tmp_path: Path) -> None:
    repository, code = project(
        tmp_path,
        {
            "site/settings.py": "ROOT_URLCONF = 'site.urls'\n",
            "site/urls.py": "from django.urls import path\n\ndef v(request): ...\n\n"
            "urlpatterns = [path('x/', v)]\n",
        },
    )
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), api_json=capture()))
    suite = TestSuiteRunner(sandbox, Images()).run(tmp_path, repository, code=code, api_probes=True)
    assert sandbox.jobs[0].test_files == {}
    assert suite.api is not None
    assert suite.api.notes[:2] == [
        "django application site.urls is not probed",
        "API probes were requested, but no endpoint of a FastAPI or Flask application that a "
        "test can import was found",
    ]


def test_browser_tests_use_the_browser_image_and_a_larger_tmp(tmp_path: Path) -> None:
    repository, code = project(tmp_path, {"requirements-dev.txt": "pytest-playwright\n"})
    sandbox, images = Sandbox(result(case("test_a", Outcome.PASSED))), Images()
    TestSuiteRunner(sandbox, images).run(tmp_path, repository, code=code)
    assert images.browser == [True]
    assert sandbox.jobs[0].tmpfs_size == "256m"
    plain_repository, plain_code = project(tmp_path / "plain", {})
    plain_sandbox, plain_images = Sandbox(result(case("test_a", Outcome.PASSED))), Images()
    TestSuiteRunner(plain_sandbox, plain_images).run(tmp_path, plain_repository, code=plain_code)
    assert (plain_images.browser, plain_sandbox.jobs[0].tmpfs_size) == ([False], "64m")
