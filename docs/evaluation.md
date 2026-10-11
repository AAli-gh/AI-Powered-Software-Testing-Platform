# Evaluation on real bugs (BugsInPy)

How well does TestMind run a real project's tests and point at the code that is wrong? This
page measures it on 135 real bugs from 8 open-source Python projects in
[BugsInPy](https://github.com/soarsmu/BugsInPy). Each bug comes with its fix and the tests that
the fix makes pass. The harness, the per-bug results and the commands to reproduce them are in
this repository; nothing here was run by hand.

## Summary

| | Bugs | Share |
|---|---|---|
| Bugs evaluated | 135 | |
| **Reproduced**: the bug's tests fail before the fix and pass after it | **65** | **48 %** |
| …of which localisation could be scored | 39 | |
| …beyond the failure-analysis cap (500 recorded failures per run) | 26 | |

Localisation, on the 39 scored bugs (TestMind's deterministic failure analysis, no LLM):

| Failure category | Bugs | Function the fix changed, ranked 1st | File the fix changed | Changed function in the top 3 frames |
|---|---|---|---|---|
| `exception_in_code` (raised in the project's code) | 15 | **10 (67 %)** | **15 (100 %)** | 10 (67 %) |
| `assertion_failure` (a test's assertion fails) | 23 | 0 | 0 | 0 |
| `exception_in_test` | 1 | 0 | 0 | 0 |
| **All** | **39** | **10 (26 %)** | **15 (38 %)** | **10 (26 %)** |

What the numbers say:

- **When the bug raises an exception in the project's code, TestMind points at the right
  file every time (15/15) and at the exact function the fix changed two times in three
  (10/15).** In the other five, it blamed a different function in the same file. For example,
  for tqdm-5 the fix changed `tqdm.__init__`, but the exception surfaced in `tqdm.__len__`.
- **When the bug makes a test's assertion fail, TestMind does not localise it at all.** The
  traceback ends in the test, so it contains no frame of production code to blame. That is
  the case for 23 of the 39 scored bugs. Closing this gap needs another signal than the
  traceback, such as coverage-based fault localisation (which code the failing tests execute
  and the passing ones do not). TestMind does not do that today.

## Method

For every bug the harness (`benchmarks/bugsinpy/`):

1. **Builds two trees.** It checks out the commit that fixed the bug, then reverses the fix,
   which gives `buggy`. Both trees have the same, fixed tests (`prepare.py`).
2. **Runs both through TestMind's HTTP API, as a user would.** It registers each tree as a
   project, analyses it and runs its whole test suite in the sandbox (`client.py`). The
   project's Python version and dependencies are whatever TestMind detects from the tree.
3. **Scores the result** against a ground truth that is computed independently of TestMind
   (`ground_truth.py`, `score.py`):
   - **Ground truth.** The lines the fix changes, mapped with Python's `ast` to the functions
     that contain them (qualnames such as `get_write_function.<locals>.write`).
   - **Reproduced.** Every test BugsInPy names for the bug fails on `buggy`, and none fails
     on `fixed`.
   - **Localisation**, on the failure group that holds the bug's tests:
     - *function*: TestMind links the failure to a function the fix changed;
     - *file*: that function is in a file the fix changed;
     - *rank*: the position of the first changed function among the group's production
       frames, innermost first.

**Environment.** Five-year-old code does not run with today's packages. Unpinned dependencies
resolve to versions released years after the bug: pytest-httpbin 2 breaks httpie's 2016 test
suite, for example. Each tree therefore also declares the environment BugsInPy reproduced the
bug in, as a project with a lock file would:

- `requirements-bugsinpy.txt`, its pinned packages, keeping only the pins that have a wheel
  for the sandbox (TestMind installs wheels only);
- `.python-version`.

This is the `--environment recorded` option of the harness. Everything else, including the
choice of Python version (3.7 for a project recorded on 3.6) and which tests to run, is TestMind's
own.

**Scope.** All bugs of PySnooper, httpie, cookiecutter, tqdm, thefuck, black, youtube-dl and
fastapi: 135 bugs. Python 3.7 for 91 of them and 3.8 for 44, as BugsInPy
recorded. The BugsInPy commit is `11c5f1e`.

## Why 70 bugs were not reproduced

| Reason | Bugs | Whose limitation |
|---|---|---|
| A required package has no wheel: black needs `commonmark<=0.5.4` (through recommonmark), fastapi pins `starlette` releases published as source only, cookiecutter-3 pins `watchdog==0.8.3` | 35 (black 22, fastapi 12, cookiecutter 1) | TestMind installs **wheels only**, so that no third-party `setup.py` ever runs ([ADR-0003](adr/0003-sandboxed-execution.md)). A deliberate security trade-off. |
| The test suite needs pytest 3: thefuck's `conftest.py` calls `request.node.get_marker`, removed in pytest 4 | 19 (thefuck-3 to 21) | TestMind runs every suite with its own pytest (7.4 on Python 3.7), because its plugins need it. |
| The recorded pins conflict with each other or with the project's own declarations | 5 (tqdm-1 to 4, fastapi-16) | The benchmark's environment. |
| The tests run, but the bug's test does not fail before the fix (or still fails after it) | 9 | youtube-dl-38 to 42 depend on the network, PySnooper-1 on the locale; cookiecutter-1 and 2 write into the current directory, which is the read-only repository in the sandbox. PySnooper-2 and httpie-4 were not investigated. |
| Other errors: pytest-httpbin applies a fixture twice (httpie-1); a test dependency the project does not declare (`yaml`, cookiecutter-4) | 2 | The project's declarations. |

**The failure-analysis cap.** Of the 65 reproduced bugs, 26 are youtube-dl bugs whose suite has
about 1,500 failing tests, its download tests that need the network. TestMind's failure plugin records the first 500
failures of a run, to keep the stored analysis bounded. The bug's test was beyond them, so
localisation could not be scored. These bugs are reported as *not analysed*, not as misses.

## What the evaluation changed in TestMind

The first run, on TestMind 1.0.0 as released, ran **none** of the first 10 bugs. Every
problem the evaluation found was fixed in general, with a regression test, never for one
benchmark project:

| Problem | Fix |
|---|---|
| Projects ran on Python 3.12 only | One sandbox image per Python version from 3.7 to 3.12, chosen from what the project declares ([test-engine.md](test-engine.md#python-versions)) |
| A project tested up to Python 3.5 ran on 3.12 | When no declared version has an image, the closest newer one is used (3.7) |
| `extras_require={"tests": {...}}`, a set, was ignored in `setup.py` | Lists, tuples and sets are read |
| `install_requires=requires`, with `requires` assigned earlier in `setup.py`, was ignored | A name bound once to a literal at module level is resolved, without running the file |
| `test_requirements.txt` was not recognised | `*-requirements.txt` and `*_requirements.txt` are read too |
| flit's legacy `[tool.flit.metadata]` was not read (fastapi until 2020) | Its `requires`, `requires-extra`, `requires-python` and classifiers are read |
| `pytest==3.2.1` or `pluggy==0.13.1` in an old requirements file broke TestMind's own plugins | pytest, coverage.py, pytest-cov and pytest's dependencies always come from the sandbox image |
| unittest's `self.assertEqual` failures were classified as `exception_in_library` | An `AssertionError` reaching a test frame is an `assertion_failure` |

The evaluation also found bugs in the harness itself, all fixed before these numbers were taken:
- a Docker crash (disk full) that failed every run after it;
- project names compared only on the first page of the project list;
- test names compared in pytest's full form, while TestMind reports `file::name`.

## Limitations of this evaluation

- **Small, uneven sample.** 39 scored bugs is enough to see the pattern above, not for
  precise percentages. youtube-dl contributes 39 of the 65 reproduced bugs.
- **Old code.** BugsInPy's bugs are several years old. Most failures to reproduce come from
  running five-year-old code with today's tools (wheels, pytest). Current projects do not
  have that problem; this evaluation does not measure them.
- **The recorded environment helps.** Without it, unpinned dependencies resolve to today's
  versions, and fewer bugs reproduce. The first run without it reproduced fewer, but it ran
  on an older TestMind, so the two are not directly comparable.
- **Only the deterministic analysis is measured.** The LLM's root-cause hypotheses
  ([failure-analysis.md](failure-analysis.md)) were not evaluated: they need an API key and
  cost money per bug.
- **Classes are not part of test ids.** TestMind reports tests as `file::name`, so the
  harness compares BugsInPy's `file::Class::test` as `file::test`.

## Reproducing

```bash
git clone https://github.com/soarsmu/BugsInPy  # the dataset (commit 11c5f1e)
make sandbox-images                             # one sandbox image per Python version
# The stack with the sandbox, with its repositories root at WORKSPACE:
TESTMIND_HOST_REPOS_DIR=$WORKSPACE TESTMIND_SANDBOX_TIMEOUT_SECONDS=600 \
  TESTMIND_RATE_LIMIT_PER_MINUTE=0 make up-sandbox
export TESTMIND_API_TOKEN=...                   # the stack's token
uv run python -m benchmarks.bugsinpy run --dataset BugsInPy --workspace $WORKSPACE \
  --cache .cache/bugsinpy --environment recorded --clean --results results.jsonl \
  --projects PySnooper,httpie,cookiecutter,tqdm,thefuck,black,youtube-dl,fastapi \
  --max-per-project 43
uv run python -m benchmarks.bugsinpy report --results results.jsonl
```

`run` is resumable: it skips bugs already in the results file. The full run takes about
ten hours on one worker; youtube-dl's suite alone takes about nine minutes per bug. The results
behind this page are in
[`benchmarks/bugsinpy/results/recorded.jsonl`](../benchmarks/bugsinpy/results/recorded.jsonl),
one line per bug with its status, ground truth, the function TestMind blamed and its rank.
