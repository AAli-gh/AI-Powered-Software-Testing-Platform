# Security

TestMind's job is to read and, from Phase 5 on, **execute** code from repositories it did not
write. The platform must assume that code is hostile.

## Threat model (summary)

| Threat | Phase 1 control | Later |
|---|---|---|
| Reading files outside the intended repositories (path traversal, symlinks) | Paths resolved with symlinks followed and required to be strictly inside `TESTMIND_REPOS_ROOT`, checked at registration and again at execution. Covered by unit and API tests | — |
| Hostile repository content during analysis | The analyzer never imports or runs repository code: `ast` parsing, `ast.literal_eval` for `setup.py`, `yaml.safe_load`, `tomllib`. Symlinks are never followed (`O_NOFOLLOW` on open), `-r` includes cannot leave the repository, and file count, size and depth are bounded ([details](repository-analyzer.md#safety-and-limits)) | — |
| Modifying analysed repositories | Repositories root mounted **read-only** in `api` and `worker` | Test execution works on a disposable copy |
| Executing untrusted repository code on the platform host | Not applicable yet: nothing is executed | Sandboxed runner containers with CPU, memory, pids, timeout, network and filesystem limits ([ADR-0003](adr/0003-sandboxed-execution.md)) |
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
- **Sandboxed execution.** Required before any repository code is run (Phase 5).
- **Rate limiting.**

## Reporting

Please report vulnerabilities privately through GitHub security advisories rather than public
issues.
