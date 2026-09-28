# Security

TestMind's job is to read and, from Phase 5 on, **execute** code from repositories it did not
write. The platform must assume that code is hostile.

## Threat model (summary)

| Threat | Phase 1 control | Later |
|---|---|---|
| Reading files outside the intended repositories (path traversal, symlinks) | Paths resolved with symlinks followed and required to be strictly inside `TESTMIND_REPOS_ROOT`, checked at registration and again at execution. Covered by unit and API tests | — |
| Hostile repository content during analysis | The analyzers never import or run repository code: `ast` parsing, `ast.literal_eval` for `setup.py`, `yaml.safe_load`, `tomllib`. Symlinks are never followed (`O_NOFOLLOW` on open), `-r` includes cannot leave the repository, and file count, size and depth are bounded ([details](repository-analyzer.md#safety-and-limits)) | — |
| Modifying analysed repositories | Repositories root mounted **read-only** in `api` and `worker` | Test execution works on a disposable copy |
| Executing untrusted code (the repository plus LLM-written tests) on the platform host | Only inside `DockerSandbox`: `--network none`, `--cap-drop ALL`, `no-new-privileges`, [TestMind's seccomp profile](#seccomp-profile), user 65534, `--read-only` root with a small `noexec` `/tmp`, repository and tests mounted read-only, memory/CPU/pids limits, wall-clock timeout with forced removal, no host environment variables. Only the JUnit file (parsed with `defusedxml`), coverage.py's JSON report and the failure records of TestMind's pytest plugin (both size-capped and schema-validated, with file names only matched against known locations and never opened) are read back. Covered by `tests/sandbox/test_docker_sandbox.py` | gVisor runtime (optional, not implemented) |
| A compromised worker using Docker to escape (the containerised worker needs Docker for the sandbox) | The worker never gets the Docker socket. It talks to the [sandbox proxy](#sandbox-proxy), which forwards only locked-down sandbox containers, operations on containers it created, and runner-image builds from TestMind's own template. Covered by `tests/unit/sandbox_proxy/` and `tests/sandbox/test_sandbox_proxy.py` (real daemon: every attack refused) ([ADR-0014](adr/0014-policy-enforcing-docker-proxy.md)) | — |
| Mutation testing runs the repository's tests hundreds of times | All mutants of a job run in **one** sandbox container with the same isolation (only the `noexec` `/tmp` tmpfs is larger, 256 MB, for the working copy). The driver verifies the original bytes before writing each mutant, restores the file afterwards, and kills each mutant's whole process group on its timeout. A whole-job timeout bounds the container. Only one size-capped, schema-validated JSON result is read back ([ADR-0010](adr/0010-single-container-mutation-driver.md)) | — |
| Instrumenting the application under test (API capture) and probing it | The `testmind_api` plugin runs only inside the sandbox; it wraps the frameworks' request entry points, always calls the original and never raises. Its records are aggregated and capped in the container, then size-capped, schema-validated and bounded again in TestMind, with test files only mapped to known locations. The probe module is generated from a template (no LLM); repository-derived strings enter it only as Python literals (`repr`) | — |
| Browser tests (Playwright) | Chromium runs in the browser sandbox image with the same isolation as every sandbox run: no network (the browser only reaches the application on 127.0.0.1), unprivileged, read-only root, resource limits; only `/tmp` is larger (256 MB). Chromium and its libraries are downloaded when the image is built, never when tests run | — |
| Code execution through a repository's git configuration (`core.fsmonitor`, `diff.external`, filters, hooks) | No git process is ever started. History is read as objects with dulwich (pure Python). `.git` files, symlinked `.git`, `alternates` and `commondir` are refused. Commits are written to temporary directories as regular files only: symlinks, submodules and unsafe names are skipped ([ADR-0009](adr/0009-read-only-git-with-dulwich.md)) | — |
| Forged or replayed GitHub webhooks | Every delivery must carry `X-Hub-Signature-256`, HMAC-SHA256 of the raw body with the webhook secret, compared in constant time **before** the payload is parsed. Bodies are capped at 5 MB. Re-deliveries are de-duplicated by a unique delivery id. Owners can be restricted (`TESTMIND_GITHUB_ALLOWED_OWNERS`). Every repository name, sha and ref is validated before it becomes part of a URL | — |
| Hostile archives from GitHub | Tarballs are size-capped while they stream, then extracted by TestMind's own extractor: only regular files and directories. Symlinks, hard links, devices, FIFOs, absolute and `..` paths, duplicates and oversize entries are skipped and listed. No `git` process runs. Imports are written through a partial directory and renamed, to a root separate from the read-only repositories root, which the API mounts read-only ([ADR-0012](adr/0012-github-app-with-tarball-snapshots.md)) | — |
| Leaking the GitHub App key or tokens | The key comes from an environment variable or a file only (`SecretStr`, never in `repr`). Installation tokens live in the worker's memory for at most their lifetime. Neither is logged, stored in the database or passed to the sandbox. The App's permissions are the minimum listed in [github.md](github.md) | Secret manager integration (Phase 14) |
| Cross-site scripting and clickjacking through the dashboard | Repository-derived text (test names, messages, tracebacks, paths) is rendered by React as text, never as HTML. Pages carry a strict Content-Security-Policy (`default-src 'self'`, no inline script or style, `frame-ancestors 'none'`), `nosniff` and `no-referrer`. The end-to-end test fails on any console error, where CSP violations appear. The dashboard's nginx runs unprivileged, read-only, without capabilities | — |
| Unauthenticated use of the API and dashboard | With `TESTMIND_API_TOKEN`, every `/api/` request needs `Authorization: Bearer <token>`, compared in constant time; production refuses to start without a token, and tokens shorter than 32 characters are refused. The dashboard asks for the token and keeps it in the tab's `sessionStorage` only ([API authentication](#api-authentication)) | Users, roles and per-user tokens |
| Exhausting workers through job floods | Requests that change state are limited per client and minute in Redis (`TESTMIND_RATE_LIMIT_PER_MINUTE`, default 60), answered `429` with `Retry-After` | Per-user quotas (with users) |
| Malicious build scripts in dependencies | Runner images install dependencies with `pip --only-binary=:all:` (wheels only), so no third-party `setup.py` runs. The build happens in a throw-away `docker build`, and tests then run from the image with no network. Through the sandbox proxy, only TestMind's Dockerfile template with plain requirement lines (no URLs, no pip options) can be built | Private package index / allow-list |
| Prompt injection via analysed code | The code is framed as data in the prompt. Acceptance never depends on the model's words (static checks plus sandbox run; a test for a surviving mutant must pass on the original code **and** kill the mutant in the sandbox; an API test counts only when the application's own record shows the request that closes the gap). Forbidden imports/calls are rejected before execution | — |
| Sensitive values reaching the LLM | Failure analysis sends source excerpts and `repr`s of up to 12 local variables of the failing frame (200 characters each) to the configured provider, and only when an AI analysis is explicitly requested. The deterministic analysis never leaves the platform. Every prompt passes [redaction](#llm-prompt-redaction) first | — |
| Leaking the LLM API key | Read only by the Anthropic SDK from its own environment variable; not part of `Settings`, never logged; not passed into the sandbox (tested) | Secret manager integration (Phase 14) |
| Container breakout / privilege escalation | Non-root user (uid 10001), read-only root FS, `no-new-privileges`, all capabilities dropped. Sandboxes: the seccomp profile below | Optional gVisor runtime |
| Leaking secrets | Secrets only in environment variables. `.env` git-ignored. `SecretStr` for connection URLs, the GitHub key and the webhook secret. Generic `500` bodies. gitleaks in pre-commit and CI | — |
| Vulnerable dependencies | Locked dependencies (`uv.lock`) audited with pip-audit in CI | — |
| Insecure deserialisation from the broker | Celery accepts JSON only (no pickle) | — |
| Network exposure | PostgreSQL and Redis not published. API and dashboard bound to `127.0.0.1`. The dashboard reaches the API on the same origin (no CORS). The sandbox proxy is reachable only on an internal network shared with the worker | TLS termination for public deployments |
| Log injection / oversized headers | Incoming `X-Request-ID` accepted only if it matches `[A-Za-z0-9._-]{1,128}` | — |

## Secrets management

- Copy `.env.example` to `.env`. Never commit `.env` (it is git-ignored and excluded from the
  Docker build context by `.dockerignore`).
- `docker-compose.yml` refuses to start without `POSTGRES_*` variables (`${VAR:?}`), so it never
  falls back to a default password.
- The GitHub App's private key and webhook secret are read through `Settings` as `SecretStr`
  (`TESTMIND_GITHUB_PRIVATE_KEY` or `…_PRIVATE_KEY_FILE`, `TESTMIND_GITHUB_WEBHOOK_SECRET`).
  A blank webhook secret is refused. The Anthropic key is read by the SDK itself.

## Sandbox proxy

The containerised worker needs Docker to run tests in the sandbox, but the Docker socket is
root on the host. So the worker never gets it: `docker-compose.sandbox.yml` (`make
up-sandbox`) adds `sandbox-proxy`, the only container with the socket. The worker's Docker CLI
talks to it over an internal network (`DOCKER_HOST=tcp://sandbox-proxy:2375`). Code:
`src/testmind/sandbox_proxy/` ([ADR-0014](adr/0014-policy-enforcing-docker-proxy.md)).

For every request, the proxy parses the HTTP request and decides:

| Request | Allowed when |
|---|---|
| `_ping`, `version` | Always |
| Inspect an image | The image is a sandbox or runner image |
| Create a container | See the list below |
| Attach, start, wait, kill, inspect, remove a container | The container carries the label the proxy puts on the containers it creates (checked with Docker before forwarding) |
| Build an image | The classic builder (`DOCKER_BUILDKIT=0`: BuildKit's sessions cannot be inspected), one `testmind-runner:…` tag, and a context of exactly TestMind's runner Dockerfile (on an allowed base) plus a `requirements.txt` of plain requirement lines |
| Anything else | Never: listing containers, exec, archives, pulls, volumes, networks, BuildKit sessions, swarm, plugins… |

A created container must:
- use a sandbox or runner image (no registry, no digest), under a `testmind-…` name;
- run as a non-root user;
- have `NetworkMode none`, `CapDrop ALL`, `no-new-privileges` and a read-only root
  filesystem;
- set memory (with no swap), CPU and pids limits, all below the proxy's caps;
- mount only bind mounts:
  - with sources inside the configured directories, which must exist and contain no
    symlink (the proxy sees the same directories at the same paths);
  - with targets under `/workspace/` (read-only) or `/out`;
- have at most a `noexec,nosuid` `/tmp` tmpfs of bounded size.

It must not use:
- privileged mode, added capabilities or devices;
- host namespaces or shared IPC;
- `seccomp=unconfined`, AppArmor or SELinux overrides;
- changed masked paths, sysctls, extra hosts, published ports, restart policies or other log
  drivers.

Any other non-empty field is refused, so a field Docker adds later fails closed. The proxy then
replaces whatever seccomp option the client sent with TestMind's profile.

Each connection carries exactly one request, forwarded with `Connection: close`. Bytes after the
request are never forwarded, so a second request cannot pass on a kept-alive connection.
Conflicting `Content-Length` / `Transfer-Encoding` and oversized heads or bodies are refused.
The one exception is the hijacked `attach` stream of a vetted container, which is relayed both
ways.

Residual risks, stated plainly:
- The proxy itself holds the socket: a bug in it is a bug in the boundary. It is small,
  standard-library only, and tested against the real daemon.
- The proxy runs as root (to open the root-owned socket) with only `DAC_READ_SEARCH` (to look
  inside the worker's private temporary directories), read-only, with `no-new-privileges`.
- Between the proxy's symlink check and Docker's mount there is a small race window. A
  compromised worker could swap a checked directory for a symlink. What it could then mount
  is read-only, into a container without network, from which it could copy data out through
  `/out`.
- Runner-image builds (pip installing wheels from PyPI) run in Docker's default build
  sandbox, with network, as before.

## Seccomp profile

Sandbox containers run with `src/testmind/sandbox/seccomp.json`: Docker's default profile
without four syscalls that it allows even when every capability is dropped, and that a test
never needs. They are `ptrace`, `process_vm_readv` and `process_vm_writev` (reading and
writing other processes) and `name_to_handle_at` (half of the "shocker" container escape).
`scripts/make_seccomp_profile.py` derives the profile from Docker's upstream file, with the
rule in `testmind.sandbox.seccomp`.

`DockerSandbox` applies it on every run, and the proxy forces it on every container.
`tests/sandbox/test_docker_sandbox.py` runs code calling these syscalls in a real container:
with TestMind's profile they fail with `EPERM`, and with Docker's default two of them succeed.

## API authentication

- `TESTMIND_API_TOKEN` (at least 32 characters, e.g. `openssl rand -hex 32`) protects all of
  `/api/`.
- The GitHub webhook authenticates with its HMAC signature instead.
- `/metrics` needs the token too; Prometheus sends it ([monitoring.md](monitoring.md)).
- `/health`, `/ready` and the API reference stay open.
- `TESTMIND_ENV=production` refuses to start without a token.
- Development may run without one, and then the API must stay on localhost.
- There is one shared token, not users. Anyone holding it has full access.

## Rate limiting

Requests that change state (`POST`, `PUT`, `PATCH`, `DELETE` under `/api/`) are counted per
client address and minute in Redis. The count is shared by every API process. Beyond
`TESTMIND_RATE_LIMIT_PER_MINUTE` (default 60; 0 disables it) the answer is `429` with
`Retry-After`. Reads and GitHub's deliveries are not limited: GitHub does not retry a refused
delivery.

Behind the dashboard's nginx every browser counts as the proxy's address. If Redis is
unreachable, requests are let through and a warning is logged, and readiness reports Redis
as down.

## LLM prompt redaction

Every prompt passes through `RedactingProvider` (`src/testmind/ai_engine/llm/redaction.py`)
before it reaches the provider. It removes:
- private keys;
- tokens with a known format (AWS, GitHub, Anthropic, OpenAI-style, Slack, Google, Stripe,
  JWT);
- URL passwords;
- quoted literals assigned to secret-looking names (`password = "…"`, `"api_key": "…"`);
- `SECRET=…` environment lines.

Code that only handles secrets (`token = request.headers["Authorization"]`) is kept. Only the
number of redactions is logged. It is pattern-based: a secret with no recognisable format or
name gets through.

## Not yet implemented

- **Users and authorisation.** One shared API token; no users, roles or audit trail per user.
- **gVisor or another user-space kernel** for sandboxes.
- **TLS.** Terminate it in front of TestMind for any deployment beyond localhost.

## Reporting

Please report vulnerabilities privately through GitHub security advisories rather than public
issues.
