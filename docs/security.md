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
| Code execution through a repository's git configuration (`core.fsmonitor`, `diff.external`, filters, hooks) | No git process is ever started. History is read as objects with dulwich (pure Python). `.git` files, symlinked `.git`, `alternates` and `commondir` are refused. Commits are written to temporary directories as regular files only: symlinks, submodules and unsafe names are skipped ([ADR-0009](adr/0009-read-only-git-with-dulwich.md)) | — |
| Malicious build scripts in dependencies | Runner images install dependencies with `pip --only-binary=:all:` (wheels only), so no third-party `setup.py` runs. The build happens in a throw-away `docker build`, and tests then run from the image with no network | Private package index / allow-list (Phase 13) |
| Prompt injection via analysed code | The code is framed as data in the prompt. Acceptance never depends on the model's words (static checks plus sandbox run). Forbidden imports/calls are rejected before execution | — |
| Sensitive values reaching the LLM | Failure analysis sends source excerpts and `repr`s of up to 12 local variables of the failing frame (200 characters each) to the configured provider, and only when an AI analysis is explicitly requested. The deterministic analysis never leaves the platform | Redaction rules for locals (Phase 13) |
| Leaking the LLM API key | Read only by the Anthropic SDK from its own environment variable; not part of `Settings`, never logged; not passed into the sandbox (tested) | Secret manager integration (Phase 14) |
| Container breakout / privilege escalation | Non-root user (uid 10001), read-only root FS, `no-new-privileges`, all capabilities dropped | Sandboxes: seccomp profile, optional gVisor runtime |
| Leaking secrets | Secrets only in environment variables. `.env` git-ignored. `SecretStr` for connection URLs. Generic `500` bodies. gitleaks in pre-commit and CI | LLM/GitHub tokens handled the same way |
| Vulnerable dependencies | Locked dependencies (`uv.lock`) audited with pip-audit in CI | — |
| Insecure deserialisation from the broker | Celery accepts JSON only (no pickle) | — |
| Network exposure | PostgreSQL and Redis not published. API bound to `127.0.0.1` | Authentication and authorisation (users are part of the data model roadmap) |
| Log injection / oversized headers | Incoming `X-Request-ID` accepted only if it matches `[A-Za-z0-9._-]{1,128}` | — |

## Secrets management

- Copy `.env.example` to `.env`. Never commit `.env` (it is git-ignored and excluded from the
  Docker build context by `.dockerignore`).
- `docker-compose.yml` refuses to start without `POSTGRES_*` variables (`${VAR:?}`), so it never
  falls back to a default password.
- Future API keys (LLM providers, GitHub) will be read the same way, through `Settings`, as
  `SecretStr`.

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
