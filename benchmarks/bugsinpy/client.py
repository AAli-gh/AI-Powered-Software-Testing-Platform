"""TestMind over its HTTP API, as a user drives it: register a project, analyse it, run its
tests, read the results."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
PAGE = 100  # the API's largest page for failure groups
TESTS_PAGE = 500


@dataclass(slots=True)
class SuiteRun:
    project: str
    status: str  # the job: succeeded / failed / timeout
    outcome: str | None  # the tests: passed / failed / error / install_failed / ...
    error: str | None
    seconds: float
    totals: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # node ids, failed or error
    groups: list[dict[str, Any]] = field(default_factory=list)
    python: str | None = None

    @property
    def ran_tests(self) -> bool:
        return self.outcome in {"passed", "failed"} and self.totals.get("total", 0) > 0


class TestMind:
    __test__ = False

    def __init__(self, url: str, token: str | None, *, timeout: float = 3600) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(base_url=f"{url}/api/v1", headers=headers, timeout=120)
        self._timeout = timeout

    def _get(self, path: str, **params: Any) -> Any:
        return self._http.get(path, params=params).raise_for_status().json()

    def _wait(self, path: str, started: float) -> dict[str, Any] | None:
        while time.monotonic() - started < self._timeout:
            body: dict[str, Any] = self._get(path)
            if body["status"] in TERMINAL:
                return body
            time.sleep(3)
        return None

    def _register(self, name: str) -> str:
        """A fresh project for the directory `name` of the repositories root."""
        for project in self._get("/projects", limit=100)["items"]:
            if project["name"] == name:
                self._http.delete(f"/projects/{project['id']}").raise_for_status()
        created = self._http.post("/projects", json={"name": name, "source_location": name})
        return str(created.raise_for_status().json()["id"])

    def run_suite(self, name: str) -> SuiteRun:
        started = time.monotonic()
        project = self._register(name)
        run = self._http.post(f"/projects/{project}/runs").raise_for_status().json()
        analysed = self._wait(f"/runs/{run['id']}", started)
        if analysed is None:
            return SuiteRun(name, "timeout", None, "analysis timed out", self._timeout)
        if analysed["status"] != "succeeded":
            error = analysed.get("error_message")
            return SuiteRun(name, analysed["status"], None, error, time.monotonic() - started)
        execution = self._http.post(f"/runs/{run['id']}/test-executions", json={})
        execution_id = execution.raise_for_status().json()["id"]
        body = self._wait(f"/test-executions/{execution_id}", started)
        seconds = time.monotonic() - started
        if body is None:
            return SuiteRun(name, "timeout", None, None, seconds)
        summary = body.get("summary") or {}
        result = SuiteRun(
            project=name, status=body["status"], outcome=body.get("outcome"),
            error=body.get("error_message"), seconds=seconds,
            totals=summary.get("totals") or {}, notes=summary.get("install_notes") or [],
        )  # fmt: skip
        if result.notes and result.notes[0].startswith("Python "):
            result.python = result.notes[0].split(":", 1)[0].removeprefix("Python ")
        if result.outcome in {"failed", "error"}:
            result.failed = self._failed(execution_id)
            result.groups = self._groups(execution_id)
        return result

    def _failed(self, execution_id: str) -> list[str]:
        failed: list[str] = []
        for outcome in ("failed", "error"):
            offset = 0
            while True:
                page = self._get(f"/test-executions/{execution_id}/tests", outcome=outcome,
                                 limit=TESTS_PAGE, offset=offset)  # fmt: skip
                failed += [t["nodeid"] for t in page["items"]]
                offset += TESTS_PAGE
                if offset >= page["total"]:
                    break
        return failed

    def _groups(self, execution_id: str) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self._http.get(f"/test-executions/{execution_id}/failures/groups",
                                      params={"limit": PAGE, "offset": offset})  # fmt: skip
            if response.status_code == httpx.codes.NOT_FOUND:  # no failure analysis
                return groups
            page = response.raise_for_status().json()
            groups += page["items"]
            offset += PAGE
            if offset >= page["total"]:
                return groups
