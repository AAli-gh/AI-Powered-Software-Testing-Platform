# Deployment

TestMind is deployed as the Docker Compose stack in this repository, on one Docker host. This
guide covers what changes between the development stack and a deployment others use:
- configuration and secrets;
- running the sandbox;
- TLS;
- monitoring;
- backups;
- upgrades;
- sizing.

## 1. Configuration

Copy `.env.example` to `.env` on the host (it is git-ignored) and set:

| Variable | Value |
|---|---|
| `TESTMIND_ENV` | `production`. The API then refuses to start without a token |
| `TESTMIND_API_TOKEN` | `openssl rand -hex 32`. Give it only to the people who use TestMind |
| `POSTGRES_PASSWORD` | A long random value. The database is not published outside the Docker network |
| `ANTHROPIC_API_KEY` | Optional. Without it, the AI endpoints answer `503 llm_not_configured` and everything else works |
| `TESTMIND_GITHUB_*` | Optional: the GitHub App ([github.md](github.md)) |
| `TESTMIND_PUBLIC_URL` | The HTTPS address users open, for links in pull request comments |
| `TESTMIND_HOST_REPOS_DIR` | Absolute path of the directory with the repositories to analyse (mounted read-only) |
| `TESTMIND_SANDBOX_DIR` | Absolute path of an empty directory for the worker's scratch space |

Secrets stay in `.env` or in the environment of the process that runs `docker compose`. They
are never in the images or the repository.

## 2. Start the stack with the sandbox and monitoring

```bash
make sandbox-image     # the image repository tests run in (make browser-image for Playwright)
docker compose -f docker-compose.yml -f docker-compose.sandbox.yml \
    -f docker-compose.monitoring.yml up -d --build --wait
TESTMIND_EXPECT_SANDBOX=1 TESTMIND_API_TOKEN=... ./scripts/smoke_test.sh
TESTMIND_EXPECT_SANDBOX=1 ./scripts/monitoring_check.sh
```

Setting `COMPOSE_FILE=docker-compose.yml:docker-compose.sandbox.yml:docker-compose.monitoring.yml`
in the shell makes plain `docker compose …` commands use all three.

- **The sandbox overlay** is what makes tests really run. Only the sandbox proxy has the Docker
  socket ([security.md](security.md#sandbox-proxy)). Without the overlay, executions report
  `sandbox_unavailable`.
- **The monitoring overlay** adds Prometheus on `127.0.0.1:9090`, with the alert rules
  ([monitoring.md](monitoring.md)).
- Both smoke checks must end with `OK` before anyone uses the deployment.

## 3. TLS in front

The dashboard container serves the application and the API on one origin (`127.0.0.1:8080`).
Everything is published on `127.0.0.1` only. Put a TLS-terminating reverse proxy in front of
port 8080, for example Caddy, which obtains and renews certificates by itself:

```text
# /etc/caddy/Caddyfile
testmind.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

- Users open `https://testmind.example.com`. The API is at the same address under `/api/`.
- The GitHub App's webhook URL is `https://testmind.example.com/api/v1/github/webhook`.
- Do not publish port 8000 (the API) or 9090 (Prometheus) beyond localhost. Prometheus has
  no authentication of its own.

## 4. Monitoring and logs

- **Alerts:** Prometheus evaluates the alert rules. Routing them to people (Alertmanager with
  e-mail, chat or a pager) is each deployment's choice. At the least, someone must look at
  <http://127.0.0.1:9090/alerts>, for example through an SSH tunnel.
- **Logs:** every container logs JSON lines to stdout, with a `request_id` on every API log
  line. `docker compose logs` reads them. To keep them, configure a Docker logging driver or
  a log shipper for the host.
- **Health:** `/health` is liveness; `/ready` checks PostgreSQL and Redis. Every service has a
  Docker health check and `restart: unless-stopped`.

## 5. Backups

PostgreSQL holds everything that matters: projects, jobs and their reports. Redis holds only
the queue, with append-only persistence on its volume. Back up PostgreSQL:

```bash
docker compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > testmind-$(date +%F).dump
```

Restore into a new, empty stack. Start only PostgreSQL, restore, then start the rest. The
`migrate` service finds the schema at its version and does nothing:

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < testmind-2026-09-28.dump
docker compose up -d --wait
```

This round trip was checked on a stack with 5,000 executions: the dump was 45 MB, and the
row counts and migration version matched after the restore.

- Keep dumps off the host. They contain repository code excerpts and test output.
- Jobs that were queued when the backup was taken have no queue message after a restore.
  They stay `queued`, and `TestMindJobsWaiting` fires; delete them or start them again.

## 6. Upgrades

```bash
docker compose exec -T postgres sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > before-upgrade.dump
git pull                                   # or check out the release tag
docker compose up -d --build --wait        # migrate runs before the API and worker start
./scripts/smoke_test.sh
```

- Read [CHANGELOG.md](../CHANGELOG.md) first.
- Every migration can be downgraded; a test checks this. To go back, run the old version's
  `alembic downgrade <revision>` from the API image, or restore the dump.
- **Upgrade while no job runs.** Check that `testmind_jobs{status="running"}` is 0 in
  Prometheus. Docker stops the worker 10 seconds after asking it to stop, which interrupts a
  running job. That job stays `running`. A worker that starts after the task time limit (one
  hour by default) marks it failed ([test-engine.md](test-engine.md#orphaned-jobs)), and
  `TestMindJobStuck` fires in the meantime.

## 7. Sizing

| Part | Needs |
|---|---|
| API | One process per container. It served about 45 platform overviews per second on one core of the test machine ([performance.md](performance.md)) |
| Worker | 2 concurrent jobs (`--concurrency=2` in the image). Each running sandbox uses up to `TESTMIND_SANDBOX_MEMORY` (512 MB) and `TESTMIND_SANDBOX_CPUS` (1) |
| PostgreSQL | About 17 kB per execution with a 300-test report and a 40-file coverage report, after PostgreSQL's compression. 5,000 such executions took 81 MB, and 45 MB as a dump |
| Disk for images | The runner images (one per distinct set of requirements) and the sandbox image. Docker's `image prune` removes unused ones |

To run more jobs at once, raise the worker's concurrency, or run more worker containers
(`docker compose up -d --scale worker=2`). Each concurrent sandbox needs its memory and CPU.

## What this deployment does not provide

These are stated in [security.md](security.md#not-yet-implemented):
- no user accounts: one shared token;
- no user-space kernel (gVisor) for the sandbox;
- one host: no high availability for PostgreSQL or Redis.
