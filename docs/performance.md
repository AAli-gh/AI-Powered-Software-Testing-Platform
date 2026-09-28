# Performance

The read API was measured under load against the real stack, with realistic data. Two
endpoints were far too slow. Both were fixed, and the fixes were measured the same way.

## Method

- **Tool:** [`scripts/load_test.py`](../scripts/load_test.py).
  - `seed` inserts finished jobs, built from TestMind's own report models, into the stack's
    database.
  - `run` finds ids through the API. It then sends a fixed number of GET requests to each
    endpoint, with a fixed number in flight, and prints the latency percentiles, the
    throughput and the number of errors.
- **Data:** 50 projects, 1,000 analysis runs and 5,000 test executions (five per run).
  - Every execution has a 300-test report (about 75 kB of JSON; 6 kB after PostgreSQL's
    compression) and a 40-file coverage report.
  - This is a team that has used TestMind for a few months.
- **Stack:** `docker compose` with the sandbox and monitoring overlays, and an API token.
  - One API process (uvicorn, as in the image), PostgreSQL 16 and Redis 7.
  - Everything ran on one 4-vCPU, 15 GB machine, including the load generator.
- **Load:** 300 requests per endpoint, 10 in flight, after one warm-up request each.
  - With 10 in flight on one API process, a latency includes the time a request waits
    behind the others.
  - The throughput column is the useful capacity number.

To reproduce:

```bash
make up-sandbox          # or make up; any stack with the API on 127.0.0.1:8000
docker compose run --rm --no-deps -v "$PWD/scripts:/scripts:ro" api \
    python /scripts/load_test.py seed --projects 50 --runs 20 --executions 5
TESTMIND_API_TOKEN=... uv run python scripts/load_test.py run --requests 300 --concurrency 10
```

## Results

The same data, before Phase 14 (commit `33e3a72`) and after it. Latencies are in
milliseconds.

| Endpoint | p50 before | p50 after | p95 before | p95 after | req/s before | req/s after |
|---|---:|---:|---:|---:|---:|---:|
| `GET /api/v1/overview` | **11,366** | **190** | 12,417 | 340 | **1** | **45** |
| `GET /api/v1/runs/{id}/test-executions` | **318** | **63** | 470 | 132 | **32** | **139** |
| `GET /api/v1/test-executions/{id}` | 88 | 48 | 212 | 125 | 96 | 170 |
| `GET /api/v1/projects/{id}/overview` | 157 | 114 | 291 | 251 | 59 | 77 |
| `GET /api/v1/projects` | 57 | 50 | 175 | 140 | 133 | 155 |
| `GET /api/v1/projects/{id}/runs` | 45 | 49 | 115 | 129 | 191 | 173 |
| `GET /api/v1/test-executions/{id}/tests` | 84 | 90 | 195 | 242 | 101 | 91 |
| `GET /metrics` | — | 83 | — | 180 | — | 102 |
| `GET /health` | 7 | 7 | 36 | 36 | 828 | 816 |

The endpoints that were not changed (`/projects`, `/runs`, `/tests`) move by a few
milliseconds either way between runs. That is the noise of this setup.

One request at a time (100 requests, nothing else in flight), after the fixes:

| Endpoint | p50 | p95 | p99 |
|---|---:|---:|---:|
| `GET /api/v1/overview` | 40.9 | 48.6 | 57.5 |
| `GET /api/v1/projects/{id}/overview` | 19.5 | 25.6 | 29.3 |
| `GET /api/v1/runs/{id}/test-executions` | 8.5 | 12.6 | 14.0 |
| `GET /api/v1/test-executions/{id}` | 5.5 | 7.9 | 9.6 |
| `GET /api/v1/test-executions/{id}/tests` | 9.1 | 12.3 | 14.8 |
| `GET /metrics` | 11.2 | 15.8 | 19.3 |

## What was slow, and why

### The platform overview: JSON extracted from every report

PostgreSQL's statement log (`log_min_duration_statement`) showed two queries taking almost
all of the time: each project's latest execution, and the ten most recent executions. Both
read the test totals out of the `report` JSONB. `EXPLAIN ANALYZE` of the latest-execution
query on 1,000 executions:

```text
Unique (actual time=180.776..180.883 rows=53)
  ->  Sort (actual time=180.774..180.821 rows=1003)
        ->  Hash Join (actual time=0.371..179.817 rows=1003)       <- 177 ms here
              ->  Hash Join (actual time=0.320..2.774 rows=1003)   <- the joins: 3 ms
```

The joins take 3 ms. The node above them spends 177 ms computing the output columns for all
1,003 rows, before the sort and `DISTINCT ON` keep 53. Each `report->'totals'->>'total'`
decompresses the whole 75 kB report, once per expression. That is five times per row, for
every candidate row. With 5,000 executions this took 11 seconds.

The fixes, in [`overview_repository.py`](../src/testmind/repositories/overview_repository.py):

1. **Choose, then read.** A subquery picks the executions (filter, sort, `DISTINCT ON` or
   `LIMIT`) on narrow columns. The totals are read only for the rows it returns: 53 and 10
   instead of 5,000. The same applies to the mutation-run trend of a project.
2. **Read the totals object once.** `report->'totals'` is selected as one value, and its
   fields are read in Python. One decompression per row instead of five: on 5,000
   executions, the latest-execution query took 10–14 ms instead of 26–39 ms (three runs of
   `EXPLAIN ANALYZE` each).
3. **One query for the job counts.** The counts per status of the ten job tables are one
   `UNION ALL` round trip instead of ten. `/metrics` uses the same query.

### Execution lists: four documents per row, parsed whole

Each item of a run's execution list shows a summary: totals, exit code, image, files and the
number of failure groups. To build it, the endpoint loaded every column of up to 20 rows.
That meant four JSON documents per row (report, coverage, failures, API coverage). It then
validated each whole report with its 300 tests in Pydantic.

Now [`execution_repository.py`](../src/testmind/repositories/execution_repository.py) asks
PostgreSQL for the report without `tests`, `output` and the install log (`report - '{...}'`,
`#-`). It asks for the failure groups as a count (`jsonb_array_length`). The other documents
are deferred with `raiseload`, so any code that touches them fails loudly instead of loading
them. The summary is still validated by the same `TestRunReport` model, with empty values for
the parts left out. A test checks that the answer is identical to the one built from the whole
row.

## What was not changed, and why

- **`/test-executions/{id}/tests` reads the whole report.** Its answer is the per-test list.
  At 9 ms for a 300-test report, moving its filtering and pagination into SQL would not pay
  for the complexity.
- **No new indexes.** At 5,000 executions, the scans that choose rows take 1–2 ms. An index
  on `test_executions.created_at` would matter at hundreds of thousands of executions. It
  should be added then, measured with the same tool, and not before.
- **One API process.** The image runs one uvicorn process: HTTP metrics live in its memory,
  and one process per container keeps them correct. To scale, run more API containers behind
  a load balancer. Prometheus scrapes each one, and the platform state gauges come from the
  database, so they are identical in each.

## Budgets

With 10 requests in flight on one API process, the platform overview answers at p95 in
0.35 s. It serves about 45 overviews per second. The dashboard polls it every 2 seconds per
open tab, and only while jobs are running: one API process serves some 90 such tabs.

The `TestMindApiSlow` alert ([monitoring.md](monitoring.md#alerts)) fires when the p95 of all
API requests stays above 1 s for 10 minutes.
