# Expected API testing results

The golden test is `tests/sandbox/test_api_testing.py` (real sandbox, with API probes).

## Endpoints (static discovery, confirmed by the running application)

`GET /books`, `POST /books`, `GET /books/{book_id}`, `PUT /books/{book_id}`,
`DELETE /books/{book_id}`, `GET /stats`: 6 endpoints, all with source `both`.

## API coverage of the project's own tests

3 of 6 endpoints are called (**50 %**), and no test checks an error answer (4xx).

| Gap | Endpoint | Why |
|---|---|---|
| `server_error` | `POST /books` | a probe got a 500 (see below) |
| `untested` | `PUT /books/{book_id}`, `GET /stats`, `DELETE /books/{book_id}` | no test sends a request (ranked by handler complexity: 3, 3, 1) |
| `success_only` | `GET /books/{book_id}` | only a found book is tested, never a missing one |

## Probes (11)

| Probe | Request | Expected | Answer | Verdict |
|---|---|---|---|---|
| unknown path | `GET /__testmind_probe__/no-such-path` | 404 | 404 | passed |
| wrong method | `DELETE /books`, `PATCH /books/1`, `DELETE /stats` | 405 | 405 | passed |
| empty body | `POST /books` with `{}` | 4xx | **500** | **server_error**: `KeyError: 'title'` |
| malformed body | `POST /books` with invalid JSON | 4xx | 400 | passed |
| invalid parameter | `DELETE`, `GET`, `PUT /books/not-a-number` | 4xx | 404 | passed |
| empty body | `PUT /books/1` with `{}` | 4xx | **200** | **accepted_invalid** |
| malformed body | `PUT /books/1` with invalid JSON | 4xx | **200** | **accepted_invalid** |

Both defects in `library/__init__.py` are found: `add_book` indexes the body without checking
it, and `update_book` accepts any body (`get_json(silent=True) or {}`).
