# Deployment guide

This is the canonical deployment reference for AI Almanac. Older links to
`DEPLOY_PUBLIC.md` redirect here because the former attribution-only hosting
model is no longer supported.

AI Almanac runs in one of two modes, selected by `DEPLOYMENT_MODE`:

|                              | **Personal** (default)                      | **Shared**                                |
| ---------------------------- | ------------------------------------------- | ----------------------------------------- |
| Use case                     | One operator on their own machine / GPU box | Multi-user, behind a reverse proxy        |
| Database                     | SQLite (zero config)                        | PostgreSQL (required)                     |
| Authentication               | None — local operator is admin              | Proxy OIDC (e.g. Globus via oauth2-proxy) |
| Filesystem browser           | Enabled                                     | Disabled                                  |
| LLM host-side code execution | Enabled                                     | Disabled                                  |
| Storage / runner             | Local filesystem, local process             | Local filesystem, local process           |

See [Current limitations](#current-limitations) before standing up a shared
deployment.

---

## Personal / local

### Run from source (development)

Pixi is the project environment and task manager.

```bash
pixi run dev
```

This starts SvelteKit (Vite HMR) at `http://localhost:5173` and FastAPI at
`http://localhost:8765`. The frontend talks to the API via `VITE_API_URL`.

### Run an installed build

The production wheel bundles the built SPA, so the whole app is one process:

```bash
pip install ai-almanac        # or pipx install ai-almanac
ai-almanac serve              # http://127.0.0.1:8765, opens a browser
```

`serve` binds to `127.0.0.1` only (by design — see shared mode). Useful flags:
`--port`, `--no-open`, `--reload`.

Database migrations run automatically on startup; there is no manual migration
step.

### Where data lives

Everything is under the data directory (`AI_ALMANAC_DATA_DIR`, defaults to a
per-user app directory):

```
$AI_ALMANAC_DATA_DIR/
  almanac.db        # SQLite database
  config.yaml       # UI-editable settings overlay
  jobs/             # benchmark outputs, figures, run logs
  uploads/          # user-uploaded observation datasets
  chat-figures/     # chat-generated images
```

To run real benchmarks (not the synthetic stub), set `RUNNER_MODE=pixi` and
prepare the benchmark environment once:

```bash
ai-almanac env prepare
```

### Common personal-mode settings

Set via environment, a `.env` file, or the in-app Settings page (which writes
`config.yaml`). Environment always wins over `config.yaml`.

| Setting                                      | Default           | Purpose                                                |
| -------------------------------------------- | ----------------- | ------------------------------------------------------ |
| `AI_ALMANAC_DATA_DIR`                        | per-user dir      | Root for the DB and all artifacts                      |
| `RUNNER_MODE`                                | `pixi`            | `pixi` runs real ROMP; `stub` writes synthetic outputs |
| `MAX_LOCAL_JOBS`                             | `1`               | Concurrent benchmark jobs (GPU is not oversubscribed)  |
| `OUTPUT_DIR`                                 | (data dir)`/jobs` | Move bulk outputs to a separate disk                   |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | —                 | Chat assistant (OpenAI-compatible or pydantic-ai)      |
| `CDSAPI_URL` / `CDSAPI_KEY`                  | —                 | Copernicus CDS credentials for ARCO/ERA5 obs           |

### Optional: personal device behind TLS

On an always-on box you can run `ai-almanac serve --no-open` under a process
manager and put a TLS terminator in front. A minimal systemd unit:

```ini
# /etc/systemd/system/ai-almanac.service
[Unit]
Description=AI Almanac
After=network.target

[Service]
Environment=AI_ALMANAC_DATA_DIR=/var/lib/ai-almanac
ExecStart=/usr/local/bin/ai-almanac serve --no-open
Restart=on-failure
User=ai-almanac

[Install]
WantedBy=multi-user.target
```

Front it with Caddy for automatic HTTPS:

```caddy
almanac.example.org {
    reverse_proxy 127.0.0.1:8765
}
```

This stays single-user (no authentication) — anyone who reaches the proxy is the
operator. For multiple users, use shared mode.

---

## Local shared development

Use the local shared stack to test PostgreSQL, per-user ownership, admission,
administrator permissions, sharing, quotas, uploads, and restart behavior
without configuring DNS, TLS, or an OIDC provider:

```bash
pixi run self-host-local
```

Open `http://localhost:18080`. Caddy is bound only to `127.0.0.1` and injects
one of two development identities:

- `http://localhost:18080/__dev/login/admin`
- `http://localhost:18080/__dev/login/user`

The default identity is the administrator. Visit `http://localhost:18080/__dev`
to switch identities. Using a normal and private browser window makes
cross-user ownership and sharing tests convenient.

The default stack uses `RUNNER_MODE=stub`, producing ROMP-shaped synthetic
metrics and artifacts without requiring a GPU. Sample inputs are mounted from
`./testdata` at `/datasets`.

For real local benchmark execution on a host with NVIDIA Container Toolkit:

```bash
pixi run self-host-local-gpu
```

That overlay prepares the Pixi benchmark environment in the persistent
application volume and grants the app container GPU access.

Operational commands:

```bash
pixi run self-host-local-logs
pixi run self-host-local-down
pixi run self-host-local-reset  # also deletes PostgreSQL, jobs, and uploads
```

The local identity proxy and checked-in development secrets are intentionally
unsafe for public deployment. The application remains inaccessible outside the
internal Compose network, and Caddy only listens on loopback.

---

## Shared / hosted

Shared mode adds authentication and per-user ownership. AI Almanac itself does
**not** authenticate users; it trusts identity headers set by a reverse proxy
that has already done OIDC. The reference Compose stack is **Caddy →
oauth2-proxy → AI Almanac**.

```
            ┌─────────┐   HTTPS    ┌────────┐   auth    ┌─────────────┐
  browser ──▶  Caddy  ├───────────▶ oauth2 ├──────────▶ AI Almanac │
            │  (TLS)  │            │ proxy  │  + trusted│  app:8765  │
            └─────────┘            └────────┘   headers └──────┬──────┘
                                      OIDC                    │
                                                              ▼
                                                        PostgreSQL
                                                        + data volume
```

Only Caddy publishes host ports. AI Almanac, oauth2-proxy, and PostgreSQL stay
on the internal Compose network. The proxy injects the authenticated identity
on every request:

- `X-Forwarded-User` → stable subject (OIDC `sub`)
- `X-Forwarded-Issuer` → OIDC issuer
- `X-Forwarded-Email` → email
- `X-Forwarded-Preferred-Username` → display name
- `X-Forwarded-Groups` → admission and administrator groups

A request that reaches the app **without** the subject header is rejected with
`401` — this blocks anyone who bypasses the proxy.

### Requirements

- Docker Engine with the Compose plugin.
- For real benchmark execution: NVIDIA Container Toolkit, a supported GPU, and
  the `compose.gpu.yaml` overlay. Stub-mode deployments need no GPU.
- An OIDC client whose redirect URI is
  `https://<your-host>/oauth2/callback`.
- A persistent volume for the data directory (job outputs, uploads, logs).
- Read-only observation and model data under `./datasets`.

### Start the reference stack

```bash
cp deploy/shared.env.example .env
```

The reference Compose file loads `.env` by default. To keep deployment
configuration elsewhere, set both Compose's environment file and the
application service environment file:

```bash
AI_ALMANAC_ENV_FILE=/etc/ai-almanac/shared.env \
  docker compose --env-file /etc/ai-almanac/shared.env up --build --detach --wait
```

Edit `.env` and set at least:

- `PUBLIC_HOST`
- `POSTGRES_PASSWORD` (URL-safe; the stack derives `DATABASE_URL` from it —
  set `DATABASE_URL` explicitly only for an external PostgreSQL instance)
- `OAUTH2_PROXY_OIDC_ISSUER_URL`, client ID, client secret, and cookie secret
- `ALLOWED_GROUPS`
- `ADMIN_GROUPS` and/or `ADMIN_SUBJECTS`
- unique production values for `CREDENTIAL_ENCRYPTION_KEY` and
  `CHAT_FIGURE_SIGNING_SECRET`

Then start the stack. On a GPU host, add the GPU overlay so benchmark jobs can
use the device:

```bash
docker compose up --build --detach --wait                                       # no GPU
docker compose -f compose.yaml -f compose.gpu.yaml up --build --detach --wait   # GPU host
docker compose ps
curl --fail https://almanac.example.org/ready  # use your configured PUBLIC_HOST
```

The one-shot `storage-init`, `migrate`, and `benchmark-prepare` services must
exit successfully before the application starts. `benchmark-prepare` creates
the Pixi environment used by local benchmark jobs in the persistent data
volume.

Shared mode **fails fast** unless the configuration is safe. It:

- refuses to start on SQLite (PostgreSQL is mandatory);
- forces `AUTH_MODE=proxy`;
- requires group admission and at least one administrator group or subject;
- requires non-default credential encryption and signing secrets;
- forces `ENABLE_FS_BROWSER=false` and `ENABLE_RUN_CODE=false`.

`DATABASE_URL` accepts a bare `postgresql://...` (bound to psycopg) or an
explicit `postgresql+psycopg://...`. Shared deployments run migrations through
the dedicated `migrate` service; automatic application-startup migrations are
reserved for personal mode.

Run the containerized smoke flow before deploying:

```bash
pixi run test-compose-e2e
```

This uses a disposable PostgreSQL database and stub workload, but exercises the
complete shared application flow through Caddy: identity provisioning,
data-source registration, job execution, metrics and artifacts, sharing,
restart recovery, and deletion.

### What admins vs users can do

- **Users** can run benchmarks, see their own private jobs, upload private
  datasets, and read anything shared with them.
- Jobs and uploads are **private by default**; an owner can share a job
  read-only (the "Share results" control), which never grants others the
  ability to cancel, delete, or rerun it.
- **Admins** manage the global catalog (mounted data sources, regions),
  application settings and secrets, and can inspect any record.
- The filesystem browser is admin-only and disabled in shared mode by default.

### Proxy configuration

The checked-in [Caddyfile](../deploy/Caddyfile) removes client-supplied
identity headers before inserting values returned by oauth2-proxy. If another
trusted proxy is used, configure the application header names with
`SUBMITTED_BY_HEADER`, `IDENTITY_ISSUER_HEADER`, `IDENTITY_EMAIL_HEADER`,
`IDENTITY_NAME_HEADER`, and `IDENTITY_GROUPS_HEADER`.

Any replacement proxy **must strip both the application identity headers
(`X-Forwarded-*`) and the auth-response headers it copies them from
(`X-Auth-Request-*`) on incoming requests**. Forward-auth implementations
typically only overwrite a request header when the auth service returns it, so
an unstripped client-supplied header (e.g. `X-Auth-Request-Groups` for a user
whose token has no groups claim) would otherwise pass through and escalate
privileges.

### Operations

- **Health check**: `GET /health` returns `{"status": "ok"}`.
- **Readiness check**: `GET /ready` validates the database, writable storage,
  runner, encryption key, and shared authentication configuration.
- **Backup**: `scripts/backup-shared.sh DESTINATION` writes a `pg_dump` of the
  database and an archive of the persistent data volume (artifacts live on the
  filesystem, not in PostgreSQL). Backups are taken live with zero downtime;
  the file archive may lag the database dump by a few seconds, and job
  reconciliation resolves any in-flight skew after a restore. Dataset mounts
  are read-only inputs and don't need backup. Set
  `COMPOSE="docker compose -p <project> -f <file>"` to target a non-default
  stack.
- **Restore**: `scripts/restore-shared.sh DATABASE_DUMP FILES_ARCHIVE` —
  destructive, so it asks for confirmation (pass `--yes` to skip) and writes a
  pre-restore database snapshot to a temporary directory before dropping
  anything. It stops Caddy and the app, restores both backup halves, and
  brings the stack back up.
- **Upgrade**: take a backup, build or pull the new image, then run
  `docker compose up --detach --wait`. The migration service completes before
  the new application starts. Running jobs are reconciled from the database on
  restart.
- **Rollback**: redeploy the previous version against a database backup taken
  before the upgrade (migrations are additive; a forward-only schema may not
  match an older binary).
- **Rate limiting**: the expensive paths enforce per-user limits in the
  application — upload size and stored bytes (`MAX_UPLOAD_BYTES`,
  `MAX_STORED_UPLOAD_BYTES_PER_USER`), chat requests
  (`MAX_LLM_REQUESTS_PER_MINUTE`, `MAX_CONCURRENT_LLM_REQUESTS_PER_USER`), and
  active jobs (`MAX_ACTIVE_JOBS_PER_USER`). Generic request flooding is the
  reverse proxy's job; apply connection and request limits at Caddy (or
  whatever fronts the stack) if your deployment is exposed to untrusted
  networks.
- **Audit log**: admin actions and background-maintenance failures are
  recorded in the `audit_events` table (`background.*.failed` /
  `background.*.recovered` events flag reconciler, artifact-publication, and
  upload-cleanup problems).

---

## Current limitations

- **Single execution host.** Benchmarks run as local processes on the host
  running AI Almanac, and job concurrency is gated per host by
  `MAX_LOCAL_JOBS`. A shared deployment is one application host with a locally
  mounted GPU and storage; there is no remote/worker fan-out yet. Job state and
  the capacity gate use the configured database (SQLite or PostgreSQL), so the
  supervisor works correctly on both backends.
- **Artifacts on local disk.** Job outputs, uploads, and logs live on the
  application host's filesystem (the data volume), not in object storage.
- **Deferred backends.** Object storage and remote runners (Modal, Slurm, batch
  services) are not implemented; the storage and runner interfaces are designed
  to accept them later without changing the public product concepts.

## Configuration reference

| Variable                | Mode   | Default                          | Notes                                                  |
| ----------------------- | ------ | -------------------------------- | ------------------------------------------------------ |
| `DEPLOYMENT_MODE`       | both   | `personal`                       | `personal` \| `shared`                                 |
| `AUTH_MODE`             | both   | `none`                           | `none` \| `proxy`; forced `proxy` in shared            |
| `DATABASE_URL`          | both   | SQLite                           | PostgreSQL required in shared; the reference stack derives it from `POSTGRES_PASSWORD` |
| `AI_ALMANAC_DATA_DIR`   | both   | per-user dir                     | DB (personal) + all artifacts                          |
| `ADMIN_SUBJECTS`        | shared | —                                | Comma-separated OIDC subjects                          |
| `ADMIN_EMAILS`          | shared | —                                | Comma-separated emails                                 |
| `SUBMITTED_BY_HEADER`   | both   | `X-Forwarded-User`               | Subject header                                         |
| `IDENTITY_EMAIL_HEADER` | shared | `X-Forwarded-Email`              |                                                        |
| `IDENTITY_NAME_HEADER`  | shared | `X-Forwarded-Preferred-Username` |                                                        |
| `DATASET_MOUNT_ROOTS`   | shared | —                                | Allow-list for mounted source paths                    |
| `ENABLE_FS_BROWSER`     | both   | `true`                           | Forced `false` in shared                               |
| `ENABLE_RUN_CODE`       | both   | `true`                           | Forced `false` in shared                               |
| `RUNNER_MODE`           | both   | `pixi`                           | `pixi` \| `stub`                                       |
| `MAX_LOCAL_JOBS`        | both   | `1`                              | Concurrent benchmark jobs                              |
| `APP_MEM_LIMIT`         | shared | `16g`                            | Memory ceiling for the app container (Compose)         |
| `APP_PIDS_LIMIT`        | shared | `4096`                           | Process ceiling for the app container (Compose)        |
| `OUTPUT_DIR`            | both   | (data dir)`/jobs`                | Bulk output location                                   |
| `FRONTEND_URL`          | both   | `http://localhost:5173`          | Browser origin for CORS and shared-mode request checks |
