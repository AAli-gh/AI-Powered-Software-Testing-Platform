# API reference

The API is versioned under `/api/v1`. The live OpenAPI schema is served at `/openapi.json`, with
interactive docs at `/docs` (Swagger UI) and `/redoc`.

## Conventions

- **Content type:** JSON everywhere. Unknown fields in request bodies are rejected (`422`).
- **IDs:** UUIDv4. A malformed ID in the path returns `422`.
- **Timestamps:** ISO-8601 with a UTC offset.
- **Correlation:** every response carries `X-Request-ID`. Clients may send their own
  (`[A-Za-z0-9._-]{1,128}`); any other value is replaced with a generated one.
- **Pagination:** `limit` (1–100, default 20) and `offset` (≥ 0). List responses have the shape
  `{"items": [...], "total": N, "limit": L, "offset": O}`, newest first.

## Error model

Every non-2xx response has this shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid request",
    "details": [{"loc": ["body", "name"], "msg": "String should have at least 1 character", "type": "string_too_short"}]
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_payload` | A signed GitHub delivery that is not a usable event; an invalid repository name or ref |
| 401 | `invalid_signature` | A GitHub webhook delivery without a valid `X-Hub-Signature-256` |
| 403 | `repository_not_allowed` | The repository's owner is not in `TESTMIND_GITHUB_ALLOWED_OWNERS` |
| 404 | `not_found` | Unknown project, run or route |
| 405 | `method_not_allowed` | Wrong method for a route |
| 409 | `conflict` | Duplicate project name; deleting a project with queued/running runs |
| 409 | `invalid_state_transition` | For example, cancelling a run that already started |
| 409 | `not_a_git_repository` | Regression analysis on a project that is not a plain git repository |
| 422 | `validation_error` | Body/query/path validation failed (`details` lists each problem) |
| 422 | `invalid_source` | `source_location` missing, not a directory, or outside the repositories root |
| 422 | `invalid_revision` | Unknown or malformed git revision, or base = head |
| 413 | `payload_too_large` | A webhook delivery over 5 MB |
| 502 | `github_error` | GitHub refused a request (its status and message are included) |
| 503 | `github_not_configured` | A GitHub feature was requested but the App (or the imports root) is not configured |
| 503 | `llm_not_configured` | An AI feature was requested but no LLM provider is configured |
| 503 | `dispatch_failed` | The task queue was unreachable. The run is recorded as `failed` |
| 500 | `internal_error` | Unexpected error. Details are logged, never returned |

## Endpoints

### Health

| Method | Path | Response |
|---|---|---|
| `GET` | `/health` | `200 {"status": "ok", "version": "0.1.0"}`. Process liveness only |
| `GET` | `/ready` | `200 {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}` or `503` with `"not_ready"` and the failing check(s) marked `"unavailable"` |

### Projects

`POST /api/v1/projects`

```json
{"name": "discount-service", "source_type": "local", "source_location": "discount_service"}
```

- `name`: 1–200 characters, starts with a letter or digit, then letters, digits, space, `.`,
  `_` or `-`. Surrounding whitespace is trimmed.
- `source_type`: only `local` here. GitHub projects are created by importing
  (`POST /api/v1/github/imports`, see [github.md](github.md)).
- `source_location`: a directory inside `TESTMIND_REPOS_ROOT`, given relative to it or as an
  absolute path. Stored normalised and relative to the root.

Responses: `201` with the project and a `Location` header, `409`, or `422`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/projects` | Paginated |
| `GET` | `/api/v1/projects/{project_id}` | `404` if unknown |
| `DELETE` | `/api/v1/projects/{project_id}` | `204`. `409` while any run is `queued`/`running`. Deletes its runs |

### Dashboard aggregates

Read-only summaries for the dashboard ([dashboard.md](dashboard.md)), computed in SQL from the
stored jobs. Only the totals are read from the stored reports.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/overview` | `projects` (count); `jobs` (every job table with its counts by status); `active_jobs`; `project_health` (up to 50 projects, most recent activity first, each with its latest run, latest succeeded execution with test totals and coverage, latest mutation score and active runs); `recent_executions` and `recent_pull_analyses` (10 each, newest first) |
| `GET` | `/api/v1/projects/{project_id}/overview` | `project`; `runs` (counts by status); `latest_run` and `latest_succeeded_run`; trends, oldest first: `executions` (last 50: tests, coverage, API coverage) and `mutation_runs` (last 50: score, mutants, survivors); newest first: `regressions` (last 20) and `pull_analyses` of the project's GitHub repository (last 20). `404` if the project is unknown |

### Analysis runs

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/projects/{project_id}/runs` | `202` with the run in `queued`. `404` for unknown project. `503` if the queue is down |
| `GET` | `/api/v1/projects/{project_id}/runs` | Paginated |
| `GET` | `/api/v1/runs/{run_id}` | Poll this for progress |
| `POST` | `/api/v1/runs/{run_id}/cancel` | `200` with the run in `cancelled`. `409` if it is no longer `queued` |

Run object:

```json
{
  "id": "0b9e…",
  "project_id": "5c1d…",
  "status": "succeeded",
  "error_message": null,
  "created_at": "2026-09-25T17:48:41.150Z",
  "updated_at": "2026-09-25T17:48:41.411Z",
  "started_at": "2026-09-25T17:48:41.357Z",
  "finished_at": "2026-09-25T17:48:41.411Z"
}
```

A run re-validates that the project's source is still available and confined to the
repositories root, then performs the [repository analysis](repository-analyzer.md). The run
becomes `succeeded` in the same transaction that stores the analysis.

### Repository analysis

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/runs/{run_id}/analysis` | Profile summary: languages, dependencies, frameworks, test frameworks, test files, entry points, config files, Docker, CI, issues. `404` if the run is unknown or has no analysis (queued, running, failed or cancelled) |
| `GET` | `/api/v1/runs/{run_id}/analysis/files` | File inventory, sorted by path. Query: `limit` (1–500, default 100), `offset`, `language` (e.g. `Python`), `is_test` (`true`/`false`) |

Detection object:

```json
{
  "name": "FastAPI",
  "category": "web_framework",
  "confidence": 1.0,
  "evidence": [
    "'fastapi' declared in pyproject.toml",
    "'fastapi' imported in 3 file(s): src/todo/main.py, src/todo/routers/items.py, tests/conftest.py",
    "fastapi app instance in src/todo/main.py:5"
  ]
}
```

File entry:

```json
{"path": "src/todo/main.py", "size_bytes": 213, "language": "Python", "lines": 11, "is_test": false}
```

`lines` is `null` for binary files, files larger than the read limit and files of unknown
language. The complete field reference is the OpenAPI schema (`RepositorySummary`, `FileEntry`).

### Code analysis

All list endpoints take `limit` (1–500, default 100) and `offset`, and return the standard page
shape. `404` if the run is unknown or has no code analysis (not succeeded).

| Method | Path | Query parameters | Notes |
|---|---|---|---|
| `GET` | `/api/v1/runs/{run_id}/code` | | `{"metrics": {...}, "issues": [...]}` |
| `GET` | `/api/v1/runs/{run_id}/code/findings` | `min_severity` (`low`…`critical`), `category`, `rule_id` (`TM\d{3}`), `min_confidence` (0–1), `path_prefix` | Most severe first, then path and line |
| `GET` | `/api/v1/runs/{run_id}/code/functions` | `sort` (`path` or `complexity`), `min_complexity`, `public_only`, `path_prefix` | |
| `GET` | `/api/v1/runs/{run_id}/code/classes` | `path_prefix` | |
| `GET` | `/api/v1/runs/{run_id}/code/modules` | `path_prefix` | Includes resolved imports |
| `GET` | `/api/v1/code-rules` | | Rule catalog: `[{"id": "TM010", "category": "dangerous_pattern", "title": "eval / exec"}, ...]` |

Finding object:

```json
{
  "rule_id": "TM040",
  "category": "missing_validation",
  "severity": "medium",
  "confidence": 0.5,
  "path": "inventory/stock.py",
  "line": 10,
  "symbol": "average_price",
  "message": "'count' is used as a divisor without being validated (possible ZeroDivisionError)",
  "evidence": "return total / count"
}
```

### AI test generation

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/runs/{run_id}/test-generations` | Body (optional): `{"kinds": ["unit", "edge_cases", "negative"], "max_targets": 10, "path_prefix": "src/", "coverage_execution_id": "<uuid>"}`. With `coverage_execution_id` the generation is [coverage-guided](coverage.md#coverage-guided-test-generation) (`404` if that execution does not belong to the run, `409` if it has no coverage report). `202` queued. `404` unknown run. `409` run not succeeded. `422` invalid options. `503 llm_not_configured` if no LLM credentials. `503 dispatch_failed` if the queue is down |
| `GET` | `/api/v1/runs/{run_id}/test-generations` | Paginated, newest first |
| `GET` | `/api/v1/test-generations/{generation_id}` | Status plus `result` once succeeded |

Result shape (illustrative values; the field structure is exactly what is stored):

```json
{
  "status": "succeeded",
  "provider": "anthropic",
  "model": "claude-opus-5",
  "prompt_version": "v1",
  "result": {
    "targets_selected": 3,
    "targets_available": 12,
    "input_tokens": 5120,
    "output_tokens": 1830,
    "files": [
      {
        "path": "tests/testmind/test_inventory_stock.py",
        "module": "inventory.stock",
        "targets": ["average_price", "add_item", "parse_quantity"],
        "status": "passed",
        "test_count": 9,
        "iterations": 2,
        "run": {"passed": 7, "failed": 0, "errors": 0, "xfailed": 2, "skipped": 0},
        "suspected_bugs": [
          {"test": "test_average_price_zero_count", "reason": "possible bug: count=0 raises ZeroDivisionError"}
        ],
        "attempts": [
          {"iteration": 1, "outcome": "failed", "feedback": "test_add_item_isolated: failed: …"},
          {"iteration": 2, "outcome": "passed", "feedback": null}
        ],
        "code": "import pytest\nfrom inventory.stock import …"
      }
    ]
  }
}
```

File `status` values and their meaning are listed in [ai-engine.md](ai-engine.md#file-statuses).

### Test execution

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/runs/{run_id}/test-executions` | Body (optional): `{"generation_id": "<uuid>", "api_probes": true}` to add that generation's test modules and TestMind's API probes. `202`. `404` unknown run, or a generation that does not belong to it. `409` run or generation not succeeded. `503 dispatch_failed` |
| `GET` | `/api/v1/runs/{run_id}/test-executions` | Paginated summaries, newest first |
| `GET` | `/api/v1/test-executions/{id}` | `status` (job), `outcome` (test verdict), `api_probes` and `summary` (exit code and meaning, totals, duration, image, install notes, test files, environment, `coverage_percent`, `coverage_error`, `failure_groups`, `api_coverage_percent`) |
| `GET` | `/api/v1/test-executions/{id}/tests` | Per-test results, filterable by `outcome` (`passed`, `failed`, `error`, `skipped`, `xfailed`) and `generated`. `limit` ≤ 500. `404` until a report exists |
| `GET` | `/api/v1/test-executions/{id}/logs` | `{"output": pytest console tail, "install_log": pip/docker build tail}` |

### Regression analysis

Between two commits of a project's git repository ([details](regression.md)).

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/projects/{id}/regressions` | Body: `{"base": "<ref>", "head": "HEAD", "mode": "full" \| "selected"}`; refs are a branch, tag, `HEAD` or sha, optionally `~N`, resolved to commits now. `202`; `404`; `409 not_a_git_repository`; `422 invalid_revision` (unknown, malformed, or base = head); `503 dispatch_failed` |
| `GET` | `/api/v1/projects/{id}/regressions` | Paginated, newest first |
| `GET` | `/api/v1/regressions/{id}` | Status, refs and resolved commits, `verdict` (`regressions`, `clean`, `inconclusive`), `summary` |
| `GET` | `/api/v1/regressions/{id}/report` | Changes (files, functions, module-level), selection with reasons and notes, both runs, per-test changes, failure causes (new / resolved / persisting), coverage diff, selection check. `404` until finished |
| `GET` | `/api/v1/regressions/{id}/tests` | `change` (`regressed`, `fixed`, `still_failing`, `added`, `removed`, `unchanged`, `not_run`), `limit` ≤ 500, `offset` |

### Mutation testing

On a succeeded run, on request ([details](mutation.md)).

| Method | Path | Parameters | Notes |
|---|---|---|---|
| `POST` | `/api/v1/runs/{id}/mutation-runs` | Body (optional): `{"max_mutants": 1-5000}` | `202`. The server's `TESTMIND_MUTATION_MAX_MUTANTS` wins. `404`; `409` run not succeeded; `503 dispatch_failed` |
| `GET` | `/api/v1/runs/{id}/mutation-runs` | `limit` ≤ 100, `offset` | Paginated, newest first |
| `GET` | `/api/v1/mutation-runs/{id}` | | Status, options, `score`, `summary` (baseline outcome, mutants generated, totals: killed, survived, timeout, no_coverage, errors, `score`, `covered_score`; notes) |
| `GET` | `/api/v1/mutation-runs/{id}/mutants` | `status` (`killed`, `survived`, `timeout`, `no_coverage`, `error`), `path`, `limit` ≤ 500, `offset` | Location, operator, original, replacement, tests run, killed by, duration, detail. `404` until finished |
| `GET` | `/api/v1/mutation-runs/{id}/functions` | `limit` ≤ 500, `offset` | Scores per function, weakest first, unscored last |
| `POST` | `/api/v1/mutation-runs/{id}/test-suggestions` | | Queue AI tests for the surviving mutants. `202`; `409` no survivors; `503 llm_not_configured`; `503 dispatch_failed` |
| `GET` | `/api/v1/mutation-runs/{id}/test-suggestions` · `/api/v1/test-suggestions/{id}` | | Status; `result.suggestions[]` with mutant, `status` (`accepted`, `rejected`, `llm_error`, `sandbox_error`), `test_path`, `code`, `attempts[]` (`accepted`, `fails_on_original`, `mutant_survives`, `rejected`, …) |

### API testing

HTTP endpoints of FastAPI, Flask and Django applications, how the tests call them, probes and
AI API tests ([details](api-testing.md)).

| Method | Path | Parameters | Notes |
|---|---|---|---|
| `GET` | `/api/v1/runs/{id}/api` | | Static discovery: `apps` (framework, module, variable or factory), `endpoints` (method or `ANY`, canonical path, params with types, handler, `accepts_body`, app), `notes` |
| `GET` | `/api/v1/test-executions/{id}/api-coverage` | | `frameworks`, `totals` (endpoints, tested, success_tested, error_tested, percent), `gap_count`, `top_gaps`, `unmatched_requests`, `probes` (count per verdict), `notes`. `404` with the reason when there is none |
| `GET` | `/api/v1/test-executions/{id}/api-coverage/endpoints` | `tested`, `method`, `source` (`static`, `runtime`, `both`), `limit` ≤ 500, `offset` | Per endpoint: calls, tests, statuses, generated calls, success/error tested, server errors |
| `GET` | `/api/v1/test-executions/{id}/api-coverage/gaps` | `kind` (`server_error`, `untested`, `error_only`, `success_only`) | Ranked; each with its reason, handler and complexity |
| `GET` | `/api/v1/test-executions/{id}/api-coverage/probes` | `verdict` (`passed`, `server_error`, `accepted_invalid`, `unexpected`, `not_run`) | Probe, request sent, expected answer, status received, detail |
| `GET` | `/api/v1/test-executions/{id}/api-coverage/unmatched` | | Requests of the project's tests that no route answered |
| `POST` | `/api/v1/test-executions/{id}/api-test-suggestions` | | `202`; `404` no API coverage; `409` no gap a test could close; `503 llm_not_configured`; `503 dispatch_failed` |
| `GET` | `/api/v1/test-executions/{id}/api-test-suggestions` · `/api/v1/api-test-suggestions/{id}` | | Status; `result.suggestions[]` with endpoint, gap, `status` (`accepted`, `rejected`, `llm_error`, `sandbox_error`), `test_path`, `code`, `statuses`, `attempts[]` (`accepted`, `fails_on_original`, `endpoint_not_called`, `gap_not_closed`, `rejected`, …) |

### GitHub

The GitHub App: webhooks, pull request analyses, issues, imports ([details](github.md)).

| Method | Path | Parameters | Notes |
|---|---|---|---|
| `POST` | `/api/v1/github/webhook` | headers `X-GitHub-Event`, `X-GitHub-Delivery`, `X-Hub-Signature-256` | `202 {"status": "queued", "id"}`; `200` for `pong`, `duplicate` (same delivery id, same `id`) and `ignored` (with `reason`); `400 invalid_payload`; `401 invalid_signature`; `413`; `503 github_not_configured` |
| `POST` | `/api/v1/github/pull-analyses` | body `repository`, `pull_number`, `installation_id` | `202`: analyses the pull request's current head; `403 repository_not_allowed`; `503` |
| `GET` | `/api/v1/github/pull-analyses` | `repository`, `pull_number`, `limit` ≤ 100, `offset` | Newest first |
| `GET` | `/api/v1/github/pull-analyses/{id}` | | Status, shas, `conclusion` (`success`, `failure`, `neutral`), `check_run_id`, `comment_id`, created `issues`, `summary` (regressed tests, new findings, untested changes, `suggestions`) |
| `GET` | `/api/v1/github/pull-analyses/{id}/report` | | The whole `PullReport`: regression report, `new_findings`, `untested_changes`, `suggestions`, `notes`. `404` until it has succeeded |
| `GET` | `/api/v1/github/pull-analyses/{id}/comment` | | The markdown posted on the pull request (`text/markdown`) |
| `POST` | `/api/v1/github/pull-analyses/{id}/issues` | body `suggestions` (1–50 ids) | `201` with `[{suggestion, number, url}]`. A suggestion already created returns its issue again; `404` unknown suggestion; `502 github_error` |
| `POST` | `/api/v1/github/imports` | body `repository`, `ref` (default `HEAD`), `installation_id` | `202`; the worker creates or updates the project and queues its run |
| `GET` | `/api/v1/github/imports/{id}` | | Status, `commit_sha`, `project_id`, `run_id`, `skipped` archive entries |

### Failure analysis

Every execution that ran tests has a deterministic failure report; an AI analysis is queued
on request ([details](failure-analysis.md)).

| Method | Path | Parameters | Notes |
|---|---|---|---|
| `GET` | `/api/v1/test-executions/{id}/failures` | | `failed_tests`, `analysed_tests`, `unanalysed_tests`, `notes`, `groups`, `by_category`, `decided` (groups whose blame the facts decide). `404` until tests ran |
| `GET` | `/api/v1/test-executions/{id}/failures/groups` | `category`, `limit` ≤ 100, `offset` | Groups, largest first: category, exception, crash frame, frames, locals, chain, tests, linked function, evidence, deterministic blame |
| `GET` | `/api/v1/test-executions/{id}/failures/groups/{group_id}` | | One group (`404` if unknown) |
| `POST` | `/api/v1/test-executions/{id}/failure-analyses` | | Queue an AI analysis. `202`; `409` no failure groups; `503 llm_not_configured`; `503 dispatch_failed` |
| `GET` | `/api/v1/test-executions/{id}/failure-analyses` · `/api/v1/failure-analyses/{id}` | | Status; `result.hypotheses[]` with `status` (`analysed`, `deterministic`, `skipped`, `llm_error`), `blame`, `root_cause`, `evidence[]` (`verified`), `suggested_fix`, `model_confidence`, `confidence`, `notes` |

### Coverage

Every execution that ran tests measures line and branch coverage ([details](coverage.md)).
Without a coverage report every endpoint below answers `404` with the reason (for example
`Coverage was not measured: sandbox unavailable`).

| Method | Path | Parameters | Notes |
|---|---|---|---|
| `GET` | `/api/v1/test-executions/{id}/coverage` | | Tool, threshold, totals (lines, branches, combined, functions never executed), unmeasured files, gap count and the 10 riskiest gaps |
| `GET` | `/api/v1/test-executions/{id}/coverage/files` | `below` (0–100), `sort` (`path` or `percent`), `limit` ≤ 500, `offset` | Per file: counts, percentages, `missing` ranges, `missing_lines`, branches never taken |
| `GET` | `/api/v1/test-executions/{id}/coverage/functions` | `path`, `below`, `sort`, `limit`, `offset` | Per function, with complexity and findings |
| `GET` | `/api/v1/test-executions/{id}/coverage/gaps` | `limit`, `offset` | Functions below the threshold, riskiest first, with reasons |
| `GET` | `/api/v1/test-executions/{id}/coverage/compare` | `base` (execution id, required) | Change from `base` to this execution: totals, newly covered / newly missed lines, changed files and functions |

Gap entry:

```json
{
  "path": "inventory/pricing.py",
  "qualname": "price_for",
  "line": 1,
  "percent": 0.0,
  "missing": ["2-24"],
  "missing_branches_detail": ["3->4", "3->10", "…"],
  "risk": 17.0,
  "reasons": [
    "never executed by the test suite",
    "22 of 22 branches never taken",
    "cyclomatic complexity 15",
    "static-analysis findings: TM030"
  ]
}
```

Test entry:

```json
{
  "nodeid": "tests/testmind/test_inventory_stock.py::test_average_of_zero_items",
  "file": "tests/testmind/test_inventory_stock.py",
  "line": 3,
  "name": "test_average_of_zero_items",
  "outcome": "failed",
  "duration_seconds": 0.001,
  "message": "ZeroDivisionError: division by zero",
  "traceback": "def test_average_of_zero_items():\n>       assert average_price(10, 0) == 0\n…",
  "generated": true
}
```
