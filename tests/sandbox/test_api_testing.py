"""API testing in the real sandbox: the `testmind_api` plugin inside the container, API
coverage of the example applications, the probes against examples/buggy_api (its documented
EXPECTED_API.md) and AI API tests checked by the sandbox (scripted model)."""

import asyncio
import shutil
from pathlib import Path

import pytest
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.api_engine.models import ApiCoverageReport, ProbeVerdict, Source
from testmind.api_engine.suggester import ApiTestSuggester
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.failure_engine import FailureReport
from testmind.mutation_engine.suggester import SuggestionStatus
from testmind.sandbox.docker_sandbox import DockerSandbox
from testmind.sandbox.runner_image import RunnerImageBuilder
from testmind.services.execution_service import ExecutionService
from testmind.test_engine.runner import SuiteResult, TestSuiteRunner
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.doubles import ScriptedLLM
from tests.integration.test_worker import queue_run, wait_for_terminal
from tests.sandbox.test_suite_runner import EXAMPLES, analyse
from tests.unit.analyzers.helpers import make_repo


def run_suite(sandbox: DockerSandbox, root: Path, *, api_probes: bool = False) -> SuiteResult:
    repository, code = analyse(root)
    runner = TestSuiteRunner(sandbox, RunnerImageBuilder(sandbox.image))
    return runner.run(root, repository, code=code, api_probes=api_probes)


def endpoints(
    report: ApiCoverageReport,
) -> list[tuple[str, str, Source, dict[str, int], list[str]]]:
    return [(e.method, e.path, e.source, e.statuses, e.tests) for e in report.endpoints]


@pytest.mark.parametrize(
    ("example", "expected"),
    [
        (
            "fastapi_todo",
            [
                ("GET", "/health", Source.BOTH, {"200": 1}, ["tests/test_items.py::test_health"]),
                ("GET", "/items/", Source.BOTH, {"200": 1},
                 ["tests/test_items.py::test_create_and_list"]),
                ("POST", "/items/", Source.BOTH, {"201": 1},
                 ["tests/test_items.py::test_create_and_list"]),
                ("DELETE", "/items/{item_id}", Source.BOTH, {"404": 1},
                 ["tests/test_items.py::test_delete_missing_item_is_404"]),
            ],
        ),
        (
            "flask_notes",
            [
                ("GET", "/notes", Source.BOTH, {"200": 1},
                 ["tests/test_notes.py::test_created_note_is_listed"]),
                ("POST", "/notes", Source.BOTH, {"201": 1, "400": 1},
                 ["tests/test_notes.py::test_created_note_is_listed",
                  "tests/test_notes.py::test_empty_text_is_rejected"]),
            ],
        ),
        (
            "django_blog",
            [
                ("ANY", "/api/posts/", Source.BOTH, {"200": 1},
                 ["posts/tests.py::test_only_published_posts_are_listed"]),
                ("ANY", "/api/posts/{pk}/", Source.BOTH, {"404": 1},
                 ["posts/tests.py::test_missing_post_is_404"]),
            ],
        ),
    ],
)  # fmt: skip
def test_api_coverage_of_the_examples(
    sandbox: DockerSandbox,
    example: str,
    expected: list[tuple[str, str, Source, dict[str, int], list[str]]],
) -> None:
    report = run_suite(sandbox, EXAMPLES / example).api
    assert report is not None
    # Static discovery and the running application's route table agree: every endpoint is
    # "both", and no request of the tests went unanswered.
    assert endpoints(report) == expected
    assert (report.unmatched, report.notes) == ([], [])


def test_the_fastapi_example_gaps(sandbox: DockerSandbox) -> None:
    report = run_suite(sandbox, EXAMPLES / "fastapi_todo").api
    assert report is not None
    assert [(g.kind, g.method, g.path) for g in report.gaps] == [
        ("error_only", "DELETE", "/items/{item_id}"),  # only the 404 is tested
        ("success_only", "POST", "/items/"),  # a body that is never rejected
    ]


def test_buggy_api_matches_its_expected_results(sandbox: DockerSandbox) -> None:
    """examples/buggy_api/EXPECTED_API.md, exactly."""
    result = run_suite(sandbox, EXAMPLES / "buggy_api", api_probes=True)
    report = result.api
    assert report is not None
    assert {e.source for e in report.endpoints} == {Source.BOTH}
    assert report.totals.model_dump() == {
        "endpoints": 6, "tested": 3, "success_tested": 3, "error_tested": 0, "percent": 50.0,
    }  # fmt: skip
    assert [(g.kind, g.method, g.path, g.complexity) for g in report.gaps] == [
        ("server_error", "POST", "/books", 1),
        ("untested", "PUT", "/books/{book_id}", 3),
        ("untested", "GET", "/stats", 3),
        ("untested", "DELETE", "/books/{book_id}", 1),
        ("success_only", "GET", "/books/{book_id}", 2),
    ]
    assert [(p.request, p.status, p.verdict) for p in report.probes] == [
        ("GET /__testmind_probe__/no-such-path", 404, ProbeVerdict.PASSED),
        ("DELETE /books", 405, ProbeVerdict.PASSED),
        ("PATCH /books/1", 405, ProbeVerdict.PASSED),
        ("DELETE /stats", 405, ProbeVerdict.PASSED),
        ("POST /books with body {}", 500, ProbeVerdict.SERVER_ERROR),
        ('POST /books with body {"unterminated', 400, ProbeVerdict.PASSED),
        ("DELETE /books/not-a-number", 404, ProbeVerdict.PASSED),
        ("GET /books/not-a-number", 404, ProbeVerdict.PASSED),
        ("PUT /books/not-a-number", 404, ProbeVerdict.PASSED),
        ("PUT /books/1 with body {}", 200, ProbeVerdict.ACCEPTED_INVALID),
        ('PUT /books/1 with body {"unterminated', 200, ProbeVerdict.ACCEPTED_INVALID),
    ]
    crash = report.probes[4]
    assert crash.detail is not None
    assert "KeyError: 'title'" in crash.detail
    # The probes run with the suite as generated tests; the project's own tests still pass.
    project = [t for t in result.report.tests if not t.generated]
    assert {t.outcome.value for t in project} == {"passed"}
    assert (report.unmatched, report.notes) == ([], [])


@pytest.mark.parametrize(("example", "probes"), [("fastapi_todo", 7), ("flask_notes", 4)])
def test_probes_raise_no_false_alarms(sandbox: DockerSandbox, example: str, probes: int) -> None:
    """Applications that validate their input pass every probe (FastAPI answers 422, Flask
    404 for a non-numeric parameter; both are accepted as the client error they are)."""
    result = run_suite(sandbox, EXAMPLES / example, api_probes=True)
    assert result.api is not None
    assert [p.verdict for p in result.api.probes] == [ProbeVerdict.PASSED] * probes
    assert {t.outcome.value for t in result.report.tests} == {"passed"}


FASTAPI_EDGES = {
    "pyproject.toml": '[project]\nname = "edges"\nversion = "0"\n'
    'dependencies = ["fastapi>=0.110", "httpx>=0.27"]\n',
    "edges/__init__.py": "",
    "edges/app.py": (
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\nsub = FastAPI()\n\n\n"
        "@app.get('/items/{item_id}')\ndef item(item_id: int):\n    return {'id': item_id}\n\n\n"
        "@app.get('/boom')\ndef boom():\n    raise RuntimeError('boom')\n\n\n"
        "@sub.get('/ping')\ndef ping():\n    return 'pong'\n\n\n"
        "app.mount('/sub', sub)\n"
    ),
    "tests/test_edges.py": (
        "from fastapi.testclient import TestClient\n\nfrom edges.app import app\n\n"
        "client = TestClient(app, raise_server_exceptions=False)\n\n\n"
        "def test_item():\n    assert client.get('/items/3').json() == {'id': 3}\n\n\n"
        "def test_wrong_method():\n    assert client.post('/items/3').status_code == 405\n\n\n"
        "def test_boom():\n    assert client.get('/boom').status_code == 500\n\n\n"
        "def test_mounted():\n    assert client.get('/sub/ping').json() == 'pong'\n\n\n"
        "def test_typo():\n    assert client.get('/itme/3').status_code == 404\n"
    ),
}


def test_the_plugin_on_fastapi_edge_cases(sandbox: DockerSandbox, tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo", FASTAPI_EDGES)
    result = run_suite(sandbox, root)
    assert result.report.totals.passed == 5
    report = result.api
    assert report is not None
    by_key = {(e.method, e.path): e for e in report.endpoints}
    assert by_key[("GET", "/boom")].server_errors == 1  # the exception is recorded as a 500
    assert by_key[("GET", "/items/{item_id}")].statuses == {"200": 1}
    # The mounted application's route, under the mount's prefix, found both ways.
    mounted = by_key[("GET", "/sub/ping")]
    assert (mounted.source, mounted.statuses) == (Source.BOTH, {"200": 1})
    assert [(u.method, u.path, u.status) for u in report.unmatched] == [
        ("POST", "/items/{item_id}", 405),  # the route answered, but not for this method
        ("GET", "/itme/3", 404),
    ]
    assert report.notes == []


def test_ai_api_tests_are_accepted_only_when_proven(sandbox: DockerSandbox) -> None:
    root = EXAMPLES / "buggy_api"
    repository, code = analyse(root)
    images = RunnerImageBuilder(sandbox.image)
    suite = TestSuiteRunner(sandbox, images).run(root, repository, code=code)
    assert suite.api is not None
    other = (
        "```python\nfrom library import create_app\n\ndef test_other():\n"
        "    assert create_app().test_client().get('/books').status_code == 200\n```"
    )
    update = (
        "```python\nfrom library import create_app\n\ndef test_update():\n"
        "    client = create_app().test_client()\n"
        "    response = client.put('/books/1', json={'title': 'Dune Messiah'})\n"
        "    assert response.status_code == 200\n"
        "    assert response.get_json()['title'] == 'Dune Messiah'\n```"
    )
    wrong = (
        "```python\nfrom library import create_app\n\ndef test_stats():\n"
        "    assert create_app().test_client().get('/stats').get_json()['books'] == 2\n```"
    )
    llm = ScriptedLLM([other, update, wrong, wrong])
    suggester = ApiTestSuggester(
        llm, sandbox, image=images.ensure(repository.dependencies).tag, max_tokens=100,
        max_endpoints=2,
    )  # fmt: skip
    result = suggester.suggest(root, suite.api, code.api, code)
    put, stats = result.suggestions
    assert (put.method, put.path, put.status, put.statuses) == (
        "PUT", "/books/{book_id}", SuggestionStatus.ACCEPTED, [200],
    )  # fmt: skip
    # First the model tested another endpoint: the application's own record says so.
    assert [a.outcome for a in put.attempts] == ["endpoint_not_called", "accepted"]
    assert (stats.status, [a.outcome for a in stats.attempts]) == (
        SuggestionStatus.REJECTED, ["fails_on_original", "fails_on_original"],
    )  # fmt: skip
    assert "assert 1 == 2" in (stats.attempts[0].feedback or "")


@pytest.mark.integration
@pytest.mark.usefixtures("worker")
async def test_probes_through_the_whole_stack(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    """No doubles: service → Celery → real worker → sandbox (probes + capture) → PostgreSQL."""
    shutil.copytree(EXAMPLES / "buggy_api", repos_root / "sample", dirs_exist_ok=True)
    run_id = await queue_run(session_factory, celery_app, repos_root)
    await wait_for_terminal(session_factory, run_id)
    async with session_factory() as session:
        execution = await ExecutionService(session, CeleryTaskDispatcher(celery_app)).create(
            run_id, None, api_probes=True
        )
    async with asyncio.timeout(180):
        while True:
            async with session_factory() as session:
                row = await session.get(TestExecution, execution.id)
                if row is not None and row.status.is_terminal:
                    break
            await asyncio.sleep(0.2)
    assert row.status is RunStatus.SUCCEEDED, row.error_message
    assert row.api_coverage_percent == 50.0
    report = ApiCoverageReport.model_validate(row.api_coverage)
    verdicts = [p.verdict for p in report.probes]
    assert verdicts.count(ProbeVerdict.SERVER_ERROR) == 1
    assert verdicts.count(ProbeVerdict.ACCEPTED_INVALID) == 2
    # The probe's crash is also analysed like any failure: an exception in the code.
    failures = FailureReport.model_validate(row.failures)
    assert any(g.exception_type == "KeyError" for g in failures.groups)
