# Example repositories

Small, self-contained projects used to exercise TestMind end to end. By default this directory
is mounted read-only at `/repos` inside the API and worker containers
(`TESTMIND_HOST_REPOS_DIR` in `.env`), so a project can be registered with
`"source_location": "discount_service"`.

| Directory | What it is |
|---|---|
| `discount_service/` | Tiny pure-Python package with a pytest suite (the example from the project brief). |
