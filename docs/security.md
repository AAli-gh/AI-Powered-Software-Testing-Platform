# Security

TestMind's job is to read and, from Phase 5 on, **execute** code from repositories it did not
write. The platform must assume that code is hostile.

## Threat model (summary)

| Threat | Phase 1 control | Later |
|---|---|---|
| Reading files outside the intended repositories (path traversal, symlinks) | Paths resolved with symlinks followed and required to be strictly inside `TESTMIND_REPOS_ROOT`, checked at registration and again at execution. Covered by unit and API tests | — |
| Hostile repository content during analysis | The analyzers never import or run repository code: `ast` parsing, `ast.literal_eval` for `setup.py`, `yaml.safe_load`, `tomllib`. Symlinks are never followed (`O_NOFOLLOW` on open), `-r` includes cannot leave the repository, and file count, size and depth are bounded ([details](repository-analyzer.md#safety-and-limits)) | — |
| Modifying analysed repositories | Repositories root mounted **read-only** in `api` and `worker` | Test execution works on a disposable copy |
| Executing untrusted code (the repository plus LLM-written tests) on the platform host | Only inside `DockerSandbox`: `--network none`, `--cap-drop ALL`, `no-new-privileges`, user 65534, `--read-only` root with a small `noexec` `/tmp`, repository and tests mounted read-only, memory/CPU/pids limits, wall-clock timeout with forced removal, no host environment variables. Only the JUnit file (parsed with `defusedxml`), coverage.py's JSON report and the failure records of TestMind's pytest plugin (both size-capped and schema-validated, with file names only matched against known locations and never opened) are read back. Covered by `tests/sandbox/test_docker_sandbox.py` | Per-project dependency installation (Phase 5), seccomp profile / gVisor (Phase 13) |
| Mutation testing runs the repository's tests hundreds of times | All mutants of a job run in **one** sandbox container with the same isolation (only the `noexec` `/tmp` tmpfs is larger, 256 MB, for the working copy). The driver verifies the original bytes before writing each mutant, restores the file afterwards, and kills each mutant's whole process group on its timeout. A whole-job timeout bounds the container. Only one size-capped, schema-validated JSON result is read back ([ADR-0010](adr/0010-single-container-mutation-driver.md)) | — |
| Instrumenting the application under test (API capture) and probing it | The `testmind_api` plugin runs only inside the sandbox; it wraps the frameworks' request entry points, always calls the original and never raises. Its records are aggregated and capped in the container, then size-capped, schema-validated and bounded again in TestMind, with test files only mapped to known locations. The probe module is generated from a template (no LLM); repository-derived strings enter it only as Python literals (`repr`) | — |
| Browser tests (Playwright) | Chromium runs in the browser sandbox image with the same isolation as every sandbox run: no network (the browser only reaches the application on 127.0.0.1), unprivileged, read-only root, resource limits; only `/tmp` is larger (256 MB). Chromium and its libraries are downloaded when the image is built, never when tests run | — |
| Code execution through a repository's git configuration (`core.fsmonitor`, `diff.external`, filters, hooks) | No git process is ever started. History is read as objects with dulwich (pure Python). `.git` files, symlinked `.git`, `alternates` and `commondir` are refused. Commits are written to temporary directories as regular files only: symlinks, submodules and unsafe names are skipped ([ADR-0009](adr/0009-read-only-git-with-dulwich.md)) | — |
| Forged or replayed GitHub webhooks | Every delivery must carry `X-Hub-Signature-256`, HMAC-SHA256 of the raw body with the webhook secret, compared in constant time **before** the payload is parsed. Bodies are capped at 5 MB. Re-deliveries are de-duplicated by a unique delivery id. Owners can be restricted (`TESTMIND_GITHUB_ALLOWED_OWNERS`). Every repository name, sha and ref is validated before it becomes part of a URL | — |
| Hostile archives from GitHub | Tarballs are size-capped while they stream, then extracted by TestMind's own extractor: only regular files and directories. Symlinks, hard links, devices, FIFOs, absolute and `..` paths, duplicates and oversize entries are skipped and listed. No `git` process runs. Imports are written through a partial directory and renamed, to a root separate from the read-only repositories root, which the API mounts read-only ([ADR-0012](adr/0012-github-app-with-tarball-snapshots.md)) | — |
| Leaking the GitHub App key or tokens | The key comes from an environment variable or a file only (`SecretStr`, never in `repr`). Installation tokens live in the worker's memory for at most their lifetime. Neither is logged, stored in the database or passed to the sandbox. The App's permissions are the minimum listed in [github.md](github.md) | Secret manager integration (Phase 14) |
| Cross-site scripting and clickjacking through the dashboard | Repository-derived text (test names, messages, tracebacks, paths) is rendered by React as text, never as HTML. Pages carry a strict Content-Security-Policy (`default-src 'self'`, no inline script or style, `frame-ancestors 'none'`), `nosniff` and `no-referrer`. The end-to-end test fails on any console error, where CSP violations appear. The dashboard's nginx runs unprivileged, read-only, without capabilities | Authentication (the dashboard can start jobs) |
| Malicious build scripts in dependencies | Runner images install dependencies with `pip --only-binary=:all:` (wheels only), so no third-party `setup.py` runs. The build happens in a throw-away `docker build`, and tests then run from the image with no network | Private package index / allow-list (Phase 13) |
| Prompt injection via analysed code | The code is framed as data in the prompt. Acceptance never depends on the model's words (static checks plus sandbox run; a test for a surviving mutant must pass on the original code **and** kill the mutant in the sandbox; an API test counts only when the application's own record shows the request that closes the gap). Forbidden imports/calls are rejected before execution | — |
| Sensitive values reaching the LLM | Failure analysis sends source excerpts and `repr`s of up to 12 local variables of the failing frame (200 characters each) to the configured provider, and only when an AI analysis is explicitly requested. The deterministic analysis never leaves the platform | Redaction rules for locals (Phase 13) |
| Leaking the LLM API key | Read only by the Anthropic SDK from its own environment variable; not part of `Settings`, never logged; not passed into the sandbox (tested) | Secret manager integration (Phase 14) |
| Container breakout / privilege escalation | Non-root user (uid 10001), read-only root FS, `no-new-privileges`, all capabilities dropped | Sandboxes: seccomp profile, optional gVisor runtime |
| Leaking secrets | Secrets only in environment variables. `.env` git-ignored. `SecretStr` for connection URLs, the GitHub key and the webhook secret. Generic `500` bodies. gitleaks in pre-commit and CI | — |
| Vulnerable dependencies | Locked dependencies (`uv.lock`) audited with pip-audit in CI | — |
| Insecure deserialisation from the broker | Celery accepts JSON only (no pickle) | — |
| Network exposure | PostgreSQL and Redis not published. API and dashboard bound to `127.0.0.1`. The dashboard reaches the API on the same origin (no CORS) | Authentication and authorisation (users are part of the data model roadmap) |
| Log injection / oversized headers | Incoming `X-Request-ID` accepted only if it matches `[A-Za-z0-9._-]{1,128}` | — |

## Secrets management

- Copy `.env.example` to `.env`. Never commit `.env` (it is git-ignored and excluded from the
  Docker build context by `.dockerignore`).
- `docker-compose.yml` refuses to start without `POSTGRES_*` variables (`${VAR:?}`), so it never
  falls back to a default password.
- The GitHub App's private key and webhook secret are read through `Settings` as `SecretStr`
  (`TESTMIND_GITHUB_PRIVATE_KEY` or `…_PRIVATE_KEY_FILE`, `TESTMIND_GITHUB_WEBHOOK_SECRET`).
  A blank webhook secret is refused. The Anthropic key is read by the SDK itself.

## Not yet implemented

- **Authentication/authorisation.** Do not expose the API beyond localhost or a trusted network
  until it exists.
- **Docker access for the containerised worker.** The sandbox works wherever the worker can
  reach Docker (local runs, CI). The compose stack deliberately does **not** mount the
  Docker socket (it would give the worker root on the host), so generated tests from that
  stack are stored as `unverified` until a socket proxy or rootless runtime is added
  (Phases 5 and 13).
- **Rate limiting.**

## Reporting

Please report vulnerabilities privately through GitHub security advisories rather than public
issues.
