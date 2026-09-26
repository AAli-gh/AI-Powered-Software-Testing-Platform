# Regression analysis

Phase 8 compares two commits of a project's git repository. It answers four questions:
- **What changed?** Files, and functions at AST level.
- **Which tests can that affect?** A deterministic selection, with a reason for every test.
- **What broke?** Tests that passed at base and fail at head, new failure causes, and the
  coverage change.
- **Was the selection safe?** In full mode, every regression is checked against the selection.

Code: `src/testmind/vcs/` (git access) and `src/testmind/regression/` (diff, selection,
comparison, engine).

## Reading git safely

Repositories are untrusted. The git command line can run commands from a repository's own
configuration (`core.fsmonitor`, `diff.external`, textconv filters, hooks). TestMind never
starts a git process. It reads git **objects** with **dulwich**, a pure-Python implementation
([ADR-0009](adr/0009-read-only-git-with-dulwich.md)):
- Only objects are read (commits, trees, blobs). Nothing is executed and nothing in the
  repository is written, including its working tree.
- Layouts that would redirect reads outside the repository are refused: a `.git` file
  (worktrees, submodules), a symlinked `.git`, `objects/info/alternates` and `commondir`.
- Each commit is written into its own temporary directory for the test runs, as regular files
  only:
  - symbolic links and submodules are skipped;
  - tree entries like `..` or `.git` are skipped;
  - files over the read limit are skipped, and so are files beyond the file limit.

  Everything skipped is listed in the report.

**Revisions:** a branch, a tag, `HEAD`, or a full or abbreviated sha, optionally followed by
`~N` (N first parents back). Both revisions are resolved to commits **when the job is
requested**, so moving a branch afterwards does not change what the job compares.

## Steps

```text
resolve base/head → changed files → write both commits to temp dirs → analyse both
→ run base with per-test coverage → function-level diff → select tests
→ run head (everything, or only the selection) → compare
```

### 1. Function-level diff

Each changed Python file is parsed at both commits. Functions are matched by qualname (the
code analyzer's names, e.g. `Shop.price`, `outer.<locals>.inner`) and compared by normalised
AST: `ast.dump` without positions, docstrings removed. So comments, blank lines, reformatting,
redundant parentheses and docstrings **are not changes**. Code, signatures and decorators
**are**. Module-level code (imports, constants, class attributes) is compared the same way,
with function bodies removed. A file that does not parse at one side counts as changed
throughout.

### 2. Per-test coverage

The base run uses coverage.py's dynamic contexts (`--cov-context=test`, with
[`docker/coveragerc_contexts`](../docker/coveragerc_contexts)). Coverage then records which test
executed each line, and TestMind maps that onto functions: `path::qualname` → tests. Module-level
code runs while pytest collects, outside any test, so it has no per-test attribution. Changes
there go through the import graph instead.

### 3. Selection rules

| Change | Selected |
|---|---|
| Dependency manifest, `pyproject.toml`, `setup.*`, lock files, `pytest.ini` / `tox.ini`, any `conftest.py`, the Django settings module | **Everything** |
| Test file added or modified | That file |
| Existing function modified or removed | The tests that **executed it at base** (pytest nodeids) |
| Function or file added, module-level change, unparsable file, or no per-test coverage at base | Test files that **import the module**, directly or through other repository modules (transitively; importing `pkg.sub` also loads `pkg`) |

Every selected target lists its reasons, for example `executed discount (modified) at base` or
`imports shop.pricing (changed)`. Changes that no test reaches become **notes**, for example
`shop/unused.py::helper (modified) was not executed by any test at base`. They are never
silently dropped.

### 4. Head run and comparison

- **full** (default): runs every test at head. The report also contains a **selection check**:
  every test that regressed, and whether the selection contained it (`missed` must be empty).
- **selected**: runs only the selection. pytest collects only the nodeid when it is given a
  file and a nodeid inside it, so nodeids whose whole file is selected are dropped from the
  arguments. Tests that exist but were not selected are `not_run`. If nothing is affected,
  nothing runs at head.

| Test change | Meaning |
|---|---|
| `regressed` | not failing at base, failing at head |
| `fixed` | failing at base, not failing at head |
| `still_failing` / `unchanged` | as named |
| `added` / `removed` | only at head / only at base (its file is gone) |
| `not_run` | selected mode, not selected |

Failure causes are compared by the stable group ids of the failure analysis (Phase 7):
`new`, `resolved`, `persisting`. Coverage is compared in full mode. The changed functions carry
their coverage at head, so **untested new code** is visible.

**Verdict:** `regressions` if any test regressed. `inconclusive` if either side could not run
(no sandbox, dependencies not installable, timeout). Otherwise `clean`.

## Verified example

`tests/regression_fixture.py` is a real two-commit history. `discount` gets a bug, `tax` gets
only a comment and a docstring, `rebate` is added, and an untested `helper` changes. In the
real sandbox (`tests/sandbox/test_regression_engine.py`):
- Changed functions are `discount`, `rebate` and `helper`. `tax` is correctly not a change.
- Selected:
  - `test_discount` and `TestCart::test_total`, which executed `discount` at base (the
    latter only indirectly, through `cart.total`);
  - both test files importing `shop.pricing`, because of the new `rebate`.

  `test_label` is not selected.
- **Two regressions** were found, and the selection check reports `missed: []`. In selected
  mode only 3 of the 4 tests ran, with the same verdict.
- `rebate` shows 0 % coverage at head, and the note on `helper` says no test executed it.

The same history also runs through the real Celery worker
(`tests/sandbox/test_regression_end_to_end.py`).

## Limits

- Python only for the function-level diff. Other changed files are listed. Configuration
  files select everything, and anything else selects nothing.
- A change inside a nested function also marks the enclosing function as modified. This can
  only select more tests.
- Renames appear as a delete plus an add. A function whose code moved to other lines keeps its
  identity (qualname), but a failure group's id includes the crash line, so a moved failure
  shows as resolved plus new.
- Per-test coverage is not attributed to tests outside pytest's rootdir. This never happens in
  regression runs, which only run the repository's own tests.
- The bundled `examples/` are plain directories, not git repositories. The API answers
  `409 not_a_git_repository` for them, and the smoke test checks exactly that.

## API

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/projects/{id}/regressions` | `{"base": "v1", "head": "HEAD", "mode": "full" \| "selected"}`. `202`. `422 invalid_revision` for an unknown revision or base = head. `409 not_a_git_repository`. `404`. `503` if the queue is down |
| `GET` | `/api/v1/projects/{id}/regressions` · `/api/v1/regressions/{id}` | Status, resolved commits, verdict, summary (changed files and functions, selected targets, regressed, fixed, new failure groups, missed by selection) |
| `GET` | `/api/v1/regressions/{id}/report` | Full report |
| `GET` | `/api/v1/regressions/{id}/tests` | Per-test comparison. `change=regressed` lists the regressions |
