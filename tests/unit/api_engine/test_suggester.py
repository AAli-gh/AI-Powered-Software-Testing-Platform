"""The API test suggester's loop and checks (scripted model, canned sandbox answers). The real
sandbox is used in tests/sandbox/test_api_testing.py."""

import json
from pathlib import Path

import pytest

from testmind.ai_engine.llm import LLMError
from testmind.analyzers.code import CodeAnalyzer, CodeProfile
from testmind.api_engine.capture import RawApiCapture, parse_api_json
from testmind.api_engine.coverage import build_api_coverage
from testmind.api_engine.models import (
    ApiApp,
    ApiCoverageReport,
    ApiGap,
    ApiSurface,
    Framework,
    Handler,
)
from testmind.api_engine.suggester import (
    ApiSuggestionResult,
    ApiTestSuggester,
    _client,
)
from testmind.mutation_engine.suggester import SuggestionStatus
from testmind.sandbox.docker_sandbox import PytestJob
from testmind.sandbox.models import Outcome, SandboxResult, SandboxUnavailableError, TestCaseResult
from tests.doubles import ScriptedLLM
from tests.unit.analyzers.helpers import make_repo

APP = """from flask import Flask, request

app = Flask(__name__)


@app.get("/items")
def items():
    return []


@app.post("/items")
def add():
    payload = request.get_json()
    if not payload:
        return {"error": "empty"}, 400
    return payload, 201


@app.delete("/items/<int:item_id>")
def remove(item_id):
    return "", 204
"""
EXISTING = (
    "from shop import app\n\n\n"
    "def test_items():\n    assert app.test_client().get('/items').status_code == 200\n"
)
TEST = "```python\nfrom shop import app\n\ndef test_x():\n    assert app.test_client()\n```"


GENERATED = "/workspace/tests/testmind/test_api_01.py"


def request_record(
    method: str, route: str, status: int, *, test: str = "test_x", file: str = GENERATED
) -> dict[str, object]:
    return {
        "test_path": file, "test_name": test, "framework": "flask", "method": method,
        "route": route, "path": route, "status": status,
    }  # fmt: skip


def run(
    *calls: dict[str, object], outcome: Outcome = Outcome.PASSED, api: bool = True
) -> SandboxResult:
    document = {"version": 1, "rootdir": "/workspace/tests", "calls": list(calls), "apps": []}
    return SandboxResult(
        exit_code=0 if outcome is Outcome.PASSED else 1,
        timed_out=False,
        duration_seconds=0.1,
        tests=[TestCaseResult(name="test_x", classname="c", outcome=outcome, message="boom")],
        output="",
        api_json=json.dumps(document) if api else None,
    )


class Sandbox:
    def __init__(self, *results: SandboxResult | Exception) -> None:
        self.results = list(results)
        self.jobs: list[PytestJob] = []

    def run(self, repo_root: Path, job: PytestJob) -> SandboxResult:
        self.jobs.append(job)
        answer = self.results.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def shop(tmp_path: Path) -> tuple[Path, ApiCoverageReport, CodeProfile]:
    root = make_repo(tmp_path, {"shop.py": APP, "tests/test_shop.py": EXISTING})
    code = CodeAnalyzer().analyze(root, ["shop.py"])
    project = "/workspace/src/tests/test_shop.py"
    recorded = capture_of(
        request_record("GET", "/items", 200, test="test_items", file=project),
        request_record("POST", "/items", 201, test="test_add", file=project),
    )
    return root, build_api_coverage(code.api, recorded, code=code), code


def capture_of(*calls: dict[str, object]) -> RawApiCapture:
    document = {"version": 1, "rootdir": "/workspace/src", "calls": list(calls), "apps": []}
    return parse_api_json(json.dumps(document))


def suggest(
    shop: tuple[Path, ApiCoverageReport, CodeProfile],
    llm: ScriptedLLM,
    sandbox: Sandbox,
    *,
    max_endpoints: int = 5,
) -> ApiSuggestionResult:
    root, report, code = shop
    suggester = ApiTestSuggester(
        llm, sandbox, image="runner:1", max_tokens=100, max_endpoints=max_endpoints
    )
    return suggester.suggest(root, report, code.api, code)


def test_gaps_and_the_prompt(shop: tuple[Path, ApiCoverageReport, CodeProfile]) -> None:
    _, report, _ = shop
    assert [(g.kind, g.method, g.path) for g in report.gaps] == [
        ("untested", "DELETE", "/items/{item_id}"),
        ("success_only", "POST", "/items"),
    ]
    llm = ScriptedLLM([TEST, TEST])
    sandbox = Sandbox(
        run(request_record("DELETE", "/items/<int:item_id>", 204)),
        run(request_record("POST", "/items", 400, file="/workspace/tests/testmind/test_api_02.py")),
    )
    result = suggest(shop, llm, sandbox)
    assert [(s.method, s.path, s.status, s.statuses) for s in result.suggestions] == [
        ("DELETE", "/items/{item_id}", SuggestionStatus.ACCEPTED, [204]),
        ("POST", "/items", SuggestionStatus.ACCEPTED, [400]),
    ]
    assert result.accepted == 2
    assert [s.test_path for s in result.suggestions] == [
        "tests/testmind/test_api_01.py", "tests/testmind/test_api_02.py",
    ]  # fmt: skip
    job = sandbox.jobs[0]
    assert (job.capture_api, job.image, list(job.test_files)) == (
        True, "runner:1", ["testmind/test_api_01.py"],
    )  # fmt: skip
    prompt = llm.requests[0].prompt
    assert "Endpoint: DELETE /items/{item_id}" in prompt
    assert "Path parameters: item_id (int)" in prompt
    assert "from shop import app\nclient = app.test_client()" in prompt
    assert (
        '@app.delete("/items/<int:item_id>")\ndef remove(item_id):\n    return "", 204'
        not in prompt
    )
    assert 'def remove(item_id):\n    return "", 204' in prompt  # the handler
    assert EXISTING.strip() in prompt  # the project's own test module, for style
    assert "check that invalid input or a missing resource is REJECTED" in llm.requests[1].prompt


def test_feedback_until_the_gap_is_closed(
    shop: tuple[Path, ApiCoverageReport, CodeProfile],
) -> None:
    llm = ScriptedLLM([TEST, TEST])
    sandbox = Sandbox(
        run(request_record("GET", "/items", 200)),  # another endpoint
        run(request_record("DELETE", "/items/<int:item_id>", 204)),
    )
    [first] = suggest(shop, llm, sandbox, max_endpoints=1).suggestions
    assert [a.outcome for a in first.attempts] == ["endpoint_not_called", "accepted"]
    assert "No passing test sent a request to DELETE /items/{item_id}" in llm.requests[1].prompt


@pytest.mark.parametrize(
    ("replies", "results", "status", "outcomes"),
    [
        (
            ["no code", "```python\ndef test_x(:\n```"],
            [],
            SuggestionStatus.REJECTED,
            ["rejected", "rejected"],
        ),
        (
            ["```python\ndef test_x():\n    assert True\n```"] * 2,
            [],
            SuggestionStatus.REJECTED,
            ["rejected", "rejected"],  # the application is never imported
        ),
        ([LLMError("down")], [], SuggestionStatus.LLM_ERROR, ["llm_error"]),
        (
            [TEST, TEST],
            [run(outcome=Outcome.FAILED), run(api=False)],
            SuggestionStatus.REJECTED,
            ["fails_on_original", "endpoint_not_called"],
        ),
        (
            [TEST],
            [SandboxUnavailableError("no docker")],
            SuggestionStatus.SANDBOX_ERROR,
            ["sandbox_error"],
        ),
    ],
)
def test_outcomes(
    shop: tuple[Path, ApiCoverageReport, CodeProfile],
    replies: list[str | Exception],
    results: list[SandboxResult | Exception],
    status: SuggestionStatus,
    outcomes: list[str],
) -> None:
    [suggestion] = suggest(
        shop, ScriptedLLM(replies), Sandbox(*results), max_endpoints=1
    ).suggestions
    assert suggestion.status is status
    assert [a.outcome for a in suggestion.attempts] == outcomes


def test_a_gap_needs_the_right_kind_of_answer(
    shop: tuple[Path, ApiCoverageReport, CodeProfile],
) -> None:
    root, report, code = shop
    success_only = report.model_copy(update={"gaps": report.gaps[1:]})
    llm = ScriptedLLM([TEST, TEST])
    wrong = request_record("POST", "/items", 201)
    sandbox = Sandbox(run(wrong), run({**wrong, "exception": False, "status": 201}))
    suggester = ApiTestSuggester(llm, sandbox, image=None, max_tokens=10, max_endpoints=1)
    [suggestion] = suggester.suggest(root, success_only, code.api, code).suggestions
    assert [a.outcome for a in suggestion.attempts] == ["gap_not_closed", "gap_not_closed"]
    assert suggestion.attempts[0].feedback == (
        "The tests only received [201] from POST /items; the gap needs a 4xx."
    )
    error_only = success_only.model_copy(
        update={"gaps": [success_only.gaps[0].model_copy(update={"kind": "error_only"})]}
    )
    sandbox = Sandbox(*[run(request_record("POST", "/items", 400))] * 2)
    [suggestion] = (
        ApiTestSuggester(
            ScriptedLLM([TEST, TEST]), sandbox, image=None, max_tokens=10, max_endpoints=1
        )
        .suggest(root, error_only, code.api, code)
        .suggestions
    )
    assert suggestion.attempts[0].feedback == (
        "The tests only received [400] from POST /items; the gap needs a successful answer "
        "(2xx or 3xx)."
    )


def test_the_limit_and_server_errors(shop: tuple[Path, ApiCoverageReport, CodeProfile]) -> None:
    root, report, code = shop
    crashing = report.model_copy(
        update={"gaps": [report.gaps[0].model_copy(update={"kind": "server_error"}), *report.gaps]}
    )
    sandbox = Sandbox(run(request_record("DELETE", "/items/<int:item_id>", 204)))
    result = ApiTestSuggester(
        ScriptedLLM([TEST]), sandbox, image=None, max_tokens=10, max_endpoints=1
    ).suggest(root, crashing, code.api, code)
    assert result.notes == ["2 endpoint gaps; the first 1 were used"]  # 500s are not targets
    assert [s.method for s in result.suggestions] == ["DELETE"]


def test_context_when_little_is_known(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"app.py": "x = 1\n"})
    llm = ScriptedLLM(["no code"] * 4)
    report = build_api_coverage(ApiSurface(), None).model_copy(
        update={
            "gaps": [
                ApiGap(
                    method="GET", path="/runtime-only", kind="untested", reason="r",
                    handler=Handler(module="gone", qualname="f", path="gone.py", line=3),
                    complexity=None,
                ),
                ApiGap(
                    method="GET", path="/unknown", kind="untested", reason="r", handler=None,
                    complexity=None,
                ),
            ]
        }
    )  # fmt: skip
    ApiTestSuggester(llm, Sandbox(), image=None, max_tokens=10, max_endpoints=2).suggest(
        root, report, ApiSurface(), None
    )
    first, second = llm.requests[0].prompt, llm.requests[2].prompt
    assert "# the handler's file could not be read" in first
    assert "# no test of the project calls the application" in first
    assert "Obtain the application the way the existing test module does." in first
    assert "# the handler was not found statically" in second


@pytest.mark.parametrize(
    ("framework", "target", "client"),
    [
        (Framework.FASTAPI, "shop.main:app",
         "from fastapi.testclient import TestClient\nfrom shop.main import app\n"
         "client = TestClient(app)"),
        (Framework.FLASK, "notes:create_app()",
         "from notes import create_app\nclient = create_app().test_client()"),
        (Framework.DJANGO, None, "Use `django.test.TestCase` and its `self.client`; the test "
         "runner creates the database and DJANGO_SETTINGS_MODULE is set."),
    ],
)  # fmt: skip
def test_how_a_test_obtains_the_application(
    framework: Framework, target: str | None, client: str
) -> None:
    app = None
    if target is not None:
        module, _, attribute = target.partition(":")
        app = ApiApp(
            framework=framework, module=module, path="x.py", line=1,
            variable=None if attribute.endswith("()") else attribute,
            factory=attribute.removesuffix("()") if attribute.endswith("()") else None,
        )  # fmt: skip
    assert _client(framework, app) == client


def test_django_and_unreadable_answers(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        {
            "site/settings.py": "ROOT_URLCONF = 'site.urls'\n",
            "site/urls.py": "from django.urls import path\n\n\ndef page(request):\n"
            "    return None\n\n\nurlpatterns = [path('page/', page)]\n",
        },
    )
    code = CodeAnalyzer().analyze(root, ["site/settings.py", "site/urls.py"])
    report = build_api_coverage(code.api, capture_of())
    django_test = (
        "```python\nfrom django.test import TestCase\n\n\nclass T(TestCase):\n"
        "    def test_x(self):\n        self.client.get('/page/')\n```"
    )
    broken = run().model_copy(update={"api_json": "{not json"})
    elsewhere = run(request_record("ANY", "page/", 200, file="/workspace/src/tests/other.py"))
    llm = ScriptedLLM([django_test, django_test])
    [suggestion] = (
        ApiTestSuggester(
            llm, Sandbox(broken, elsewhere), image=None, max_tokens=10, max_endpoints=1
        )
        .suggest(root, report, code.api, code)
        .suggestions
    )
    assert [a.outcome for a in suggestion.attempts] == [
        "endpoint_not_called",  # the capture could not be read
        "endpoint_not_called",  # the request came from another test file
    ]
    assert "Use `django.test.TestCase`" in llm.requests[0].prompt


def test_the_example_is_the_first_readable_test_file(
    shop: tuple[Path, ApiCoverageReport, CodeProfile],
) -> None:
    root, report, code = shop
    first = report.endpoints[0]
    moved = first.model_copy(update={"tests": ["tests/a_gone.py::test_x", *first.tests]})
    report = report.model_copy(update={"endpoints": [moved, *report.endpoints[1:]]})
    llm = ScriptedLLM([LLMError("stop")])
    ApiTestSuggester(llm, Sandbox(), image=None, max_tokens=10, max_endpoints=1).suggest(
        root, report, code.api, code
    )
    assert EXISTING.strip() in llm.requests[0].prompt


def test_no_module_to_import_is_required_when_none_is_known(tmp_path: Path) -> None:
    root = make_repo(tmp_path, {"app.py": "x = 1\n"})
    report = build_api_coverage(ApiSurface(), None).model_copy(
        update={
            "gaps": [
                ApiGap(
                    method="GET", path="/x", kind="untested", reason="r", handler=None,
                    complexity=None,
                )
            ]
        }
    )  # fmt: skip
    plain = "```python\ndef test_x():\n    assert True\n```"
    [suggestion] = ApiTestSuggester(
        ScriptedLLM([plain, plain]), Sandbox(run(), run()), image=None, max_tokens=10,
        max_endpoints=1,
    ).suggest(root, report, ApiSurface(), None).suggestions  # fmt: skip
    assert [a.outcome for a in suggestion.attempts] == ["endpoint_not_called"] * 2
