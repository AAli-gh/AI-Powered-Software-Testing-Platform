# GitHub integration

Phase 11 connects TestMind to GitHub as a **GitHub App**:
- **Pull request analysis.** Every pull request that is opened or updated is analysed. The
  whole test suite runs at the base and head commits, and the code analysis looks for new
  problems in the changed files. The result is posted as a **check run** and as **one pull
  request comment**, which is updated on every push.
- **Issue suggestions.** Each report proposes issues: a regressed test, a new high or critical
  finding, or changed functions that no test executes. They are created on GitHub **only on
  request**, and each at most once.
- **Repository import.** A repository at a branch, tag or commit becomes a TestMind project.
  Its analysis run is queued straight away.

Everything that decides the outcome is deterministic: the regression engine
([regression.md](regression.md)) and the code analyzer ([code-analyzer.md](code-analyzer.md)).
No LLM is involved in this phase.

Code:
- `src/testmind/github/`: the client (App authentication, REST calls, downloads), the
  webhooks, the pull request analysis and its markdown.
- `src/testmind/vcs/archive.py`: safe tarball extraction.
- `src/testmind/vcs/snapshots.py`: commits held as snapshots, for the regression engine.
- `src/testmind/services/github_service.py`: the API side.
- `src/testmind/services/github_executor.py`: the worker side.

> **Tested against a local fake of GitHub, not against github.com.** The fake
> (`tests/github_fake.py`) checks the App JWT against the public key, issues installation
> tokens, and answers only for repositories of the installation. It serves real tarballs
> through a redirect, as GitHub does. The request and response shapes follow GitHub's REST
> API documentation (version `2022-11-28`). A real App installation has not been exercised
> in this repository's tests or CI.

## 1. Setting up the App

1. Create a GitHub App (Settings → Developer settings → GitHub Apps):
   - **Webhook URL:** `https://<your TestMind>/api/v1/github/webhook`.
   - **Webhook secret:** a long random string.
   - **Repository permissions:**
     - *Contents: read*, for tarballs and commits.
     - *Pull requests: read*.
     - *Checks: read & write*.
     - *Issues: read & write*, for the pull request comment and the issues.
   - **Events:** *Pull request*.
2. Generate a private key and install the App on the repositories to analyse.
3. Configure TestMind. The variables are in `.env.example`. The key and the secret belong in
   `.env` or your secret store, never in the repository.

| Variable | |
|---|---|
| `TESTMIND_GITHUB_APP_ID` | The App's id |
| `TESTMIND_GITHUB_PRIVATE_KEY` or `TESTMIND_GITHUB_PRIVATE_KEY_FILE` | The PEM key. In a variable, use one line with `\n` for the line breaks |
| `TESTMIND_GITHUB_WEBHOOK_SECRET` | The webhook secret (must not be blank) |
| `TESTMIND_GITHUB_ALLOWED_OWNERS` | Comma-separated users or organisations whose repositories may be analysed. Empty: any installation |
| `TESTMIND_GITHUB_IMPORTS_ROOT` | Where imported repositories are written. Unset: imports are disabled. docker compose uses the `github-imports` volume |
| `TESTMIND_PUBLIC_URL` | Optional: links from the comment to the report |
| `TESTMIND_GITHUB_API_URL`, `TESTMIND_GITHUB_CHECK_NAME`, `TESTMIND_GITHUB_MAX_ARCHIVE_MB` | GitHub Enterprise, the check run's name, the archive size cap (200 MB) |

Without the App, every GitHub endpoint answers `503 github_not_configured` and names the
missing variable.

## 2. Authentication

TestMind authenticates the way GitHub specifies for Apps:
1. It signs a JWT with the private key: RS256, issuer = App id, a lifetime of 9 minutes,
   backdated 60 s for clock drift.
2. It exchanges the JWT for an **installation access token**
   (`POST /app/installations/{id}/access_tokens`).
3. The token is cached per installation until 5 minutes before it expires.

The key and the tokens never reach the database, the logs or the sandbox. `AppCredentials`
does not print its key, and settings hold it as `SecretStr`.

## 3. Webhook

`POST /api/v1/github/webhook` checks, in this order:

1. **Configured?** A secret is set, or the answer is `503`.
2. **Size.** At most 5 MB, or `413`.
3. **Signature.** `X-Hub-Signature-256` is HMAC-SHA256 of the raw body with the secret.
   It is compared in constant time. Missing or wrong: `401 invalid_signature`. Nothing in
   the payload is read before this check.
4. **Event:**
   - `ping` → `pong`;
   - `pull_request` → parsed;
   - anything else → `ignored`.
5. **Action.** `opened`, `synchronize`, `reopened` and `ready_for_review` are analysed.
   Other actions and draft pull requests are `ignored`, with the reason.
6. **Owner.** An owner outside `TESTMIND_GITHUB_ALLOWED_OWNERS` is `ignored`.
7. **Delivery id.** A delivery GitHub sends again (same `X-GitHub-Delivery`) returns
   `duplicate` with the existing analysis. The id is unique in the database, so concurrent
   re-deliveries cannot create two analyses.

A queued analysis answers `202 {"status": "queued", "id": …}`. All other outcomes answer 200,
so GitHub does not retry deliveries that were understood.

A signed payload that is not a usable event (not JSON, missing fields, an invalid repository
name or sha) is `400 invalid_payload`. Every value that ends up in a URL is validated first:
- `owner/name`, where `.` and `..` are not names;
- 40-hex shas;
- references without `..`.

## 4. Pull request analysis

A worker runs the analysis:
1. It reads the pull request, then keeps the base and head shas of the delivery, even if the
   pull request moved on since.
2. It creates the check run (`in_progress`) on the head commit.
3. It downloads both commits as **tarballs** and extracts them safely (§6). No `git` process
   runs, and nothing is cloned.
4. It runs `RegressionEngine.compare` in `full` mode on the two snapshots:
   - every test runs at both commits in the Docker sandbox, with coverage;
   - the tests are compared and failures grouped;
   - the selection check reports what a selective run would have missed.
5. It runs the code analysis at both commits. **New findings** are those at head, in changed
   files, that the base does not have. Findings are matched by rule, file, enclosing symbol
   and the offending code, not by line number, so code that only moved is not new.
6. **Untested changes:** added or modified functions that no test executes at head
   (coverage 0).
7. The **conclusion** is:
   - `failure` when a test regressed or a new high or critical finding appeared;
   - `neutral` when the tests could not run (no sandbox, dependencies, time-out);
   - `success` otherwise.
8. It completes the check run (conclusion, title, and the markdown as summary). It then
   **updates the previous TestMind comment** on the pull request, or posts one if there is
   none or it was deleted.

The comment starts with `<!-- testmind:pull-analysis -->`. It contains:
- tests and coverage at base and head;
- the regressions and the new failure causes;
- the fixed tests;
- the new findings and the untested changes;
- the selection check;
- the suggested issues and their ids.

Lists are capped at 20 rows.

If anything fails after the check run exists, the check run is completed as `neutral` with
the reason, so it never hangs "in progress". The analysis is then `failed` with the message.
Unexpected errors are logged and reported without details.

Measured on the fixture in `tests/pull_fixture.py`, with the real sandbox, end to end through
Celery:
- 2 regressed tests;
- 1 new critical finding (`eval` of input);
- 3 untested changed functions;
- conclusion `failure`;
- the selection check missed nothing.

## 5. Issues

`POST /api/v1/github/pull-analyses/{id}/issues` with `{"suggestions": ["<id>", …]}` creates
the chosen issues, labelled `testmind`.

Suggestion ids are derived from the problem, not the run, so the same problem has the same id
on every analysis. Each created issue is recorded as soon as GitHub confirms it. A retry,
even after a partial failure, never creates the same issue twice. A GitHub refusal answers
`502 github_error` with GitHub's status and message.

## 6. Tarballs: safe extraction

An archive from GitHub is still input TestMind did not write. `extract_archive`:
- strips GitHub's top directory;
- writes only regular files and directories;
- skips, and lists in `skipped`:
  - symbolic links and hard links, devices and FIFOs;
  - absolute paths and paths with `..`;
  - duplicate or conflicting entries;
  - files over the analyzer's size limit, and files beyond its file-count limit.

The download itself is capped (`TESTMIND_GITHUB_MAX_ARCHIVE_MB`) while it streams, and
redirects are followed (GitHub serves archives from codeload). Snapshots are materialised in
temporary directories, which the sandbox mounts read-only.

## 7. Imports

`POST /api/v1/github/imports` with `{"repository": "acme/shop", "ref": "main",
"installation_id": 1}` queues an import. The worker:
1. resolves the ref to a commit;
2. downloads it into a hidden partial directory, then renames it to
   `<imports root>/acme/shop/<sha>`. A snapshot is complete or absent, and a commit that was
   imported before is not downloaded again;
3. creates the project `acme/shop` (`source_type: "github"`, location
   `github:acme/shop/<sha>`), or updates it to the new commit;
4. queues the project's analysis run.

`GET /api/v1/github/imports/{id}` returns the commit, the project, the run and the skipped
entries. From then on the project behaves like a local one for every feature. The imports
root is separate from the read-only repositories root: the API mounts it read-only, and only
the worker writes it.

## 8. API

| Method | Path | |
|---|---|---|
| POST | `/api/v1/github/webhook` | GitHub deliveries (signed) |
| POST | `/api/v1/github/pull-analyses` | Analyse a pull request's current head without a webhook |
| GET | `/api/v1/github/pull-analyses?repository=&pull_number=` | List (newest first) |
| GET | `/api/v1/github/pull-analyses/{id}` | Status, check run, comment, issues, summary |
| GET | `/api/v1/github/pull-analyses/{id}/report` | The full report |
| GET | `/api/v1/github/pull-analyses/{id}/comment` | The markdown (`text/markdown`) |
| POST | `/api/v1/github/pull-analyses/{id}/issues` | Create issues from suggestions |
| POST | `/api/v1/github/imports` | Import a repository as a project |
| GET | `/api/v1/github/imports/{id}` | The import |

## 9. Limits

- Only Python test suites are run (pytest), as everywhere in TestMind.
- The sandbox is required for test results. Without it, the analysis still reports the new
  findings, and its conclusion is `neutral` unless a finding makes it `failure`.
- **Forks.** The head of a pull request from a fork is downloaded through the base
  repository's installation, which GitHub allows for pull request heads. This path has not
  been tested against GitHub.
- Posting to GitHub is best effort for the failure path only. On the success path, a refused
  comment fails the analysis with GitHub's message.
- Imported projects are snapshots. They are refreshed by importing again, not by pushes.
