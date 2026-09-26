# ADR-0008: Capture failures as structured data inside the sandbox

**Status:** Accepted

## Context

Failure analysis (Phase 7) needs, for every failed test, the exception type, the traceback
frames with file, line and function, and ideally the values that led to the failure. The JUnit
report contains a formatted traceback. Its layout depends on `--tb`, the pytest version, the
project's plugins and whether assertion rewriting applied, and it has no reliable separation
between frames or between the repository and libraries.

## Decision

1. The sandbox image contains a small pytest plugin (`docker/testmind_failures.py`, not part
   of the `testmind` package) that records failures from pytest's hooks
   (`pytest_runtest_makereport`, `pytest_collectreport`) while the exception object is still
   available: the type, message, cause chain, traceback entries and a few `repr`-truncated
   locals.
2. The plugin writes JSON to the sandbox's output directory. TestMind treats it as untrusted
   input: size cap, schema and length validation, and paths used only as keys.
3. Text parsing is limited to where pytest offers nothing else: import and syntax errors
   during collection. There only the documented `E   Type: message` line and the location
   are extracted.
4. The plugin is loaded explicitly (`-p testmind_failures`), with its directory first on
   `PYTHONPATH`, and only for suite executions (`capture_failures`).

## Consequences

- Categories, grouping and linking work on exact data, and a golden fixture
  (`tests/unit/failure_engine/data/capture.json`) is checked against a live sandbox run.
- The plugin depends only on stable pytest APIs (`ExceptionInfo.traceback`, `item.location`,
  hook wrappers). A project that pins a very old pytest in its runner image could break it.
  Then the failures are listed as unanalysed with a note, never guessed.
- A timeout or a crashed interpreter leaves no file. Those runs are reported without groups.
