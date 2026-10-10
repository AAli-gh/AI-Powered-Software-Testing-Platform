"""Scoring a bug from two suite runs, and reading those runs from TestMind's API."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from benchmarks.bugsinpy.client import SuiteRun, TestMind
from benchmarks.bugsinpy.ground_truth import GroundTruth, Location
from benchmarks.bugsinpy.score import BugResult, score, summarise

PATH = "pkg/rules.py"
TRUTH = GroundTruth(lines={PATH: {15}}, functions={Location(PATH, "Rule.get_new_command")})
TARGET = "tests/test_rules.py::test_get_new_command"


def frame(path: str, function: str, location: str = "production") -> dict[str, str]:
    return {"path": path, "function": function, "location": location}


def group(function: tuple[str, str] | None, frames: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "category": "exception_in_code",
        "tests": [{"nodeid": f"{TARGET}[case-1]"}],
        "function": {"path": function[0], "qualname": function[1]} if function else None,
        "frames": frames,
    }


def run(failed: list[str], groups: list[dict[str, Any]] | None = None, **kw: Any) -> SuiteRun:
    defaults: dict[str, Any] = {"outcome": "failed" if failed else "passed",
                                "totals": {"total": 10}, "python": "3.8"}  # fmt: skip
    return SuiteRun("p", "succeeded", error=None, seconds=1.0, failed=failed,
                    groups=groups or [], **(defaults | kw))  # fmt: skip


def test_a_reproduced_bug_blamed_on_the_fixed_function() -> None:
    frames = [frame("tests/test_rules.py", "test_get_new_command", "test"),
              frame(PATH, "get_new_command"), frame("pkg/utils.py", "helper")]  # fmt: skip
    buggy = run([f"{TARGET}[case-1]", "tests/test_env.py::test_home"],
                [group((PATH, "Rule.get_new_command"), frames)])  # fmt: skip
    fixed = run(["tests/test_env.py::test_home"])  # fails in both: not the bug's
    result = score("p-1", [TARGET], TRUTH, buggy, fixed)
    assert (result.status, result.function_hit, result.file_hit) == ("reproduced", True, True)
    assert result.blamed == f"{PATH}::Rule.get_new_command"
    assert result.rank == 2  # innermost production frame first: helper, then get_new_command
    assert (result.category, result.python, result.seconds) == ("exception_in_code", "3.8", 2.0)


def test_blame_elsewhere_and_no_linked_function() -> None:
    elsewhere = run(
        [TARGET], [group(("pkg/utils.py", "helper"), [frame("pkg/utils.py", "helper")])]
    )
    result = score("p-1", [TARGET], TRUTH, elsewhere, run([]))
    assert (result.function_hit, result.file_hit, result.rank) == (False, False, None)
    unlinked = score("p-1", [TARGET], TRUTH, run([TARGET], [group(None, [])]), run([]))
    assert (unlinked.blamed, unlinked.function_hit) == (None, False)
    no_group = score("p-1", [TARGET], TRUTH, run([TARGET]), run([]))
    assert no_group.detail == "no failure group holds the bug's tests"


@pytest.mark.parametrize(
    ("buggy", "fixed", "status"),
    [
        (run([], outcome="install_failed", totals={}), run([]), "buggy_not_run"),
        (run([TARGET]), run([], outcome="error", totals={}), "fixed_not_run"),
        (run([]), run([]), "not_reproduced"),  # the bug's test passes on the buggy tree
        (run([TARGET]), run([TARGET]), "not_reproduced"),  # and still fails after the fix
    ],
)
def test_runs_that_do_not_reproduce(buggy: SuiteRun, fixed: SuiteRun, status: str) -> None:
    assert score("p-1", [TARGET], TRUTH, buggy, fixed).status == status


def test_summarise() -> None:
    results = [
        BugResult("a", "reproduced", python="3.8", category="exception_in_code",
                  blamed="x", function_hit=True, file_hit=True, rank=1),
        BugResult("b", "reproduced", python="3.8", category="assertion_failure", rank=4),
        BugResult("c", "buggy_not_run", python="3.7"),
    ]  # fmt: skip
    summary = summarise(results)
    assert summary["statuses"]["reproduced"] == 2
    assert summary["statuses"]["buggy_not_run"] == 1
    assert (summary["function_at_1"], summary["file_at_1"]) == (50.0, 50.0)
    assert (summary["rank_at_3"], summary["rank_at_5"]) == (50.0, 100.0)
    assert summary["no_function_linked"] == 1
    assert summary["python"] == {"3.8": 2, "3.7": 1}
    assert summarise([])["function_at_1"] is None


class FakeTestMind:
    """TestMind's API for one project: what the harness reads, with paging."""

    def __init__(self, *, analysis: str = "succeeded", outcome: str = "failed") -> None:
        self.analysis, self.outcome = analysis, outcome
        self.polls = 0
        self.deleted: list[str] = []
        self.failed = [f"t.py::test_{i}" for i in range(501)]

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 (a router)
        path, method = request.url.path.removeprefix("/api/v1"), request.method
        params = request.url.params
        if path == "/projects" and method == "GET":
            return httpx.Response(200, json={"items": [{"id": "old", "name": "p-1-buggy"}]})
        if path == "/projects/old" and method == "DELETE":
            self.deleted.append("old")
            return httpx.Response(204)
        if path == "/projects":
            assert json.loads(request.content) == {
                "name": "p-1-buggy",
                "source_location": "p-1-buggy",
            }
            return httpx.Response(201, json={"id": "p"})
        if path == "/projects/p/runs":
            return httpx.Response(202, json={"id": "r", "status": "queued"})
        if path == "/runs/r":
            self.polls += 1
            status = "running" if self.polls == 1 else self.analysis
            return httpx.Response(200, json={"status": status, "error_message": "boom"})
        if path == "/runs/r/test-executions":
            return httpx.Response(202, json={"id": "e"})
        if path == "/test-executions/e":
            summary: dict[str, Any] = {
                "totals": {"total": 600},
                "install_notes": ["Python 3.8: pinned to 3.8"],
            }
            execution = {"status": "succeeded", "outcome": self.outcome, "error_message": None}
            return httpx.Response(200, json={**execution, "summary": summary})
        if path == "/test-executions/e/tests":
            failed = self.failed if params["outcome"] == "failed" else []
            offset, limit = int(params["offset"]), int(params["limit"])
            items = [{"nodeid": n} for n in failed[offset : offset + limit]]
            return httpx.Response(200, json={"items": items, "total": len(failed)})
        if path == "/test-executions/e/failures/groups":
            offset = int(params["offset"])
            total = 150 if self.outcome == "failed" else 0
            if self.outcome == "error":
                return httpx.Response(404, json={})
            groups = [{"id": i} for i in range(offset, min(offset + 100, total))]
            return httpx.Response(200, json={"items": groups, "total": total})
        raise AssertionError(f"unexpected {method} {path}")


def client(fake: FakeTestMind, monkeypatch: pytest.MonkeyPatch) -> TestMind:
    monkeypatch.setattr("benchmarks.bugsinpy.client.time.sleep", lambda _: None)
    testmind = TestMind("http://testmind", "token")
    testmind._http = httpx.Client(
        base_url="http://testmind/api/v1", transport=httpx.MockTransport(fake)
    )
    return testmind


def test_run_suite_reads_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeTestMind()
    result = client(fake, monkeypatch).run_suite("p-1-buggy")
    assert fake.deleted == ["old"]  # a project left by an earlier run is replaced
    assert (result.status, result.outcome, result.python) == ("succeeded", "failed", "3.8")
    assert len(result.failed) == 501  # two pages of tests
    assert len(result.groups) == 150  # two pages of groups
    assert result.ran_tests


def test_run_suite_without_failures_or_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    passed = client(FakeTestMind(outcome="passed"), monkeypatch).run_suite("p-1-buggy")
    assert (passed.failed, passed.groups) == ([], [])
    errored = client(FakeTestMind(outcome="error"), monkeypatch).run_suite("p-1-buggy")
    assert errored.groups == []  # no failure analysis: 404
    assert not errored.ran_tests
    failed = client(FakeTestMind(analysis="failed"), monkeypatch).run_suite("p-1-buggy")
    assert (failed.status, failed.outcome, failed.error) == ("failed", None, "boom")


def test_run_suite_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    testmind = client(FakeTestMind(), monkeypatch)
    testmind._timeout = 0
    assert testmind.run_suite("p-1-buggy").status == "timeout"
