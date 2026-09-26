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
| 404 | `not_found` | Unknown project, run or route |
| 405 | `method_not_allowed` | Wrong method for a route |
| 409 | `conflict` | Duplicate project name; deleting a project with queued/running runs |
| 409 | `invalid_state_transition` | For example, cancelling a run that already started |
| 422 | `validation_error` | Body/query/path validation failed (`details` lists each problem) |
| 422 | `invalid_source` | `source_location` missing, not a directory, or outside the repositories root |
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
- `source_type`: only `local` is supported (GitHub sources arrive in Phase 11).
- `source_location`: a directory inside `TESTMIND_REPOS_ROOT`, given relative to it or as an
  absolute path. Stored normalised and relative to the root.

Responses: `201` with the project and a `Location` header, `409`, or `422`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/projects` | Paginated |
| `GET` | `/api/v1/projects/{project_id}` | `404` if unknown |
| `DELETE` | `/api/v1/projects/{project_id}` | `204`. `409` while any run is `queued`/`running`. Deletes its runs |

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
