# ADR-0009: Read git history with dulwich, never with the git command line

**Status:** Accepted

## Context

Regression analysis (Phase 8) needs a repository's history: resolve revisions, list the files
that changed between two commits, read file contents, and produce each commit's files for a
test run. The repositories are untrusted. The git command line runs commands taken from the
repository's own configuration and attributes: `core.fsmonitor` (even on `git status`),
`diff.external`, textconv and filter drivers, and hooks. Disabling each of them with `-c` flags
is fragile, because a single missed setting executes repository-controlled code on the worker.

## Decision

1. Use **dulwich**, a pure-Python git implementation, to read **objects only**: commits, trees
   and blobs. No git process is started. No filter, driver or hook is run, and nothing is
   written to the repository.
2. Refuse layouts that point reads elsewhere on the host: a `.git` file (`gitdir:`), a symlinked
   `.git`, `objects/info/alternates` and `commondir`.
3. Produce commits for test runs by writing the blobs into a fresh temporary directory, as
   regular files only. Symbolic links, submodules and unsafe entry names are skipped and
   reported. The working tree is never checked out or modified.
4. Resolve revisions when a job is requested, and store the commit shas.
5. Test fixtures build repositories from objects too (`tests/gitrepo.py`). dulwich's porcelain
   `commit` reads the user's global git configuration (commit signing, for example), which
   tests must not depend on.

## Consequences

- One more runtime dependency (dulwich, audited with pip-audit like the others).
- A small, documented revision syntax (`<ref or sha>[~N]`) instead of the full git syntax.
- Worktrees, submodules and shared object stores are not supported, and the API says so
  (`409 not_a_git_repository`).
