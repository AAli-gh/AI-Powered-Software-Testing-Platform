# ADR-0012: Integrate as a GitHub App, and read commits as tarball snapshots

**Status:** Accepted

## Context

Phase 11 must analyse pull requests and import repositories from GitHub. Two choices shape
everything else: how TestMind authenticates, and how it obtains a commit's files.

**Authentication.** A personal access token or an OAuth App acts as a user. It carries that
user's access to every repository, does not expire on its own, and cannot post check runs.

**Obtaining files.** Cloning with `git` runs an external program on remote content. Hooks,
filters and submodules are configuration surface that phase 9 deliberately avoided
([ADR-0009](0009-read-only-git-with-dulwich.md)). A clone also fetches history the analysis
never reads.

## Decision

1. **A GitHub App.**
   - Short-lived JWTs (RS256) are exchanged for installation tokens, which are cached until 5
     minutes before they expire.
   - Access is limited to the installations and the permissions granted to the App.
   - Check runs and comments appear as the App.
   - Webhooks are accepted only with a valid HMAC-SHA256 signature, compared in constant time
     before the payload is parsed. Re-deliveries are de-duplicated by a unique delivery id in
     the database.
2. **Tarballs, not clones.**
   - Each commit is downloaded as GitHub's tarball, with the size capped while it streams.
   - It is extracted by TestMind's own extractor, which writes only regular files and
     directories. Links, special files, unsafe paths, duplicates and oversize entries are
     skipped and listed.
   - No `git` process ever runs.
3. **Snapshots behind the regression engine's commit source.** The engine reads commits
   through a `CommitSource` protocol, which offers changes, file reads and materialisation:
   - `GitRepository` implements it for local repositories;
   - `SnapshotSource` implements it for downloaded snapshots.

   Pull request analysis is the same deterministic comparison as `/regressions`, in `full`
   mode.
4. **Results go back as a check run and one comment**, updated on each push. Issues are only
   *suggested*, and are created when a user asks. TestMind never files issues on its own.
5. **Imports are content-addressed:** `<imports root>/<owner>/<repo>/<sha>`, written through a
   partial directory and a rename. The imports root is separate from the read-only
   repositories root, and only the worker writes it.

## Consequences

- No user credentials are stored. Revoking the App's installation revokes TestMind's access.
- A commit costs one archive download per analysis, and no history is fetched. History-based
  features (such as `HEAD~1`) do not apply to imported snapshots: those projects compare
  imports, not git revisions.
- The same safe-extraction code handles every archive. It is tested with symlinks, hard
  links, absolute paths, `..`, duplicates and size limits.
- Tests run against a local fake of GitHub that verifies the JWT, the tokens and the
  installation's access. The integration has not been exercised against github.com, and
  the documentation says so.
