# Deployment guide

AI Almanac runs in one of two modes, selected by `DEPLOYMENT_MODE`:

| | **Personal** (default) | **Shared** |
| --- | --- | --- |
| Use case | One operator on their own machine / GPU box | Multi-user, behind a reverse proxy |
| Database | SQLite (zero config) | PostgreSQL (required) |
| Authentication | None — local operator is admin | Proxy OIDC (e.g. Globus via oauth2-proxy) |
| Filesystem browser | Enabled | Disabled |
| LLM host-side code execution | Enabled | Disabled |
| Storage / runner | Local filesystem, local process | Local filesystem, local process |

Personal mode is the supported path for actually running benchmarks today. See
[Current limitations](#current-limitations) before standing up a shared
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

| Setting | Default | Purpose |
| --- | --- | --- |
| `AI_ALMANAC_DATA_DIR` | per-user dir | Root for the DB and all artifacts |
| `RUNNER_MODE` | `pixi` | `pixi` runs real ROMP; `stub` writes synthetic outputs |
| `MAX_LOCAL_JOBS` | `1` | Concurrent benchmark jobs (GPU is not oversubscribed) |
| `OUTPUT_DIR` | (data dir)`/jobs` | Move bulk outputs to a separate disk |
| `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | — | Chat assistant (OpenAI-compatible or pydantic-ai) |
| `CDSAPI_URL` / `CDSAPI_KEY` | — | Copernicus CDS credentials for ARCO/ERA5 obs |

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

## Shared / hosted

Shared mode adds authentication and per-user ownership. AI Almanac itself does
**not** authenticate users; it trusts identity headers set by a reverse proxy
that has already done OIDC. The reference stack is **Caddy → oauth2-proxy →
AI Almanac**, with Globus as the OIDC provider.

```
            ┌─────────┐   HTTPS    ┌────────┐   auth    ┌──────────────┐
  browser ──▶  Caddy  ├───────────▶ oauth2 ├──────────▶ AI Almanac    │
            │  (TLS)  │            │ proxy  │  + X-     │ 127.0.0.1:8765│
            └─────────┘            └────────┘  Forwarded└──────┬───────┘
                                    (Globus           headers  │
                                     OIDC)                     ▼
                                                         PostgreSQL
                                                         + data volume
```

AI Almanac binds to `127.0.0.1` and is only reachable through the proxy. The
proxy injects the authenticated identity on every request:

- `X-Forwarded-User` → stable subject (OIDC `sub`)
- `X-Forwarded-Email` → email
- `X-Forwarded-Preferred-Username` → display name

A request that reaches the app **without** the subject header is rejected with
`401` — this blocks anyone who bypasses the proxy.

### Requirements

- PostgreSQL 14+.
- A reverse proxy doing OIDC and forwarding the identity headers above.
- A persistent volume for the data directory (job outputs, uploads, logs).
- One or more admin identities.

### Required configuration

```bash
DEPLOYMENT_MODE=shared
DATABASE_URL=postgresql+psycopg://almanac:secret@localhost/almanac
# Admins — at least one of these must be non-empty:
ADMIN_SUBJECTS=globus-sub-1,globus-sub-2
ADMIN_EMAILS=admin@example.org

# Where uploads/outputs live (back this up):
AI_ALMANAC_DATA_DIR=/var/lib/ai-almanac

# Admin-managed, read-only dataset mounts. Registered data-source paths must
# resolve within one of these roots (path traversal is rejected):
DATASET_MOUNT_ROOTS=/data/obs,/data/models
```

On startup, shared mode **fails fast** unless the configuration is safe. It:

- refuses to start on SQLite (PostgreSQL is mandatory);
- forces `AUTH_MODE=proxy`;
- requires at least one admin (`ADMIN_SUBJECTS` or `ADMIN_EMAILS`);
- forces `ENABLE_FS_BROWSER=false` and `ENABLE_RUN_CODE=false`.

`DATABASE_URL` accepts a bare `postgresql://...` (bound to psycopg) or an
explicit `postgresql+psycopg://...`. Migrations run automatically on startup.

### What admins vs users can do

- **Users** can run benchmarks, see their own private jobs, upload private
  datasets, and read anything shared with them.
- Jobs and uploads are **private by default**; an owner can share a job
  read-only (the "Share results" control), which never grants others the
  ability to cancel, delete, or rerun it.
- **Admins** manage the global catalog (mounted data sources, regions),
  application settings and secrets, and can inspect any record.
- The filesystem browser is admin-only and disabled in shared mode by default.

### Reference oauth2-proxy + Caddy

oauth2-proxy configured for Globus, passing identity upstream:

```ini
# oauth2-proxy.cfg
provider = "oidc"
oidc_issuer_url = "https://auth.globus.org"
client_id = "<globus-client-id>"
client_secret = "<globus-client-secret>"
email_domains = ["*"]
cookie_secret = "<32-byte-random>"
upstreams = ["http://127.0.0.1:8765"]
pass_user_headers = true
set_xauthrequest = true
# Map OIDC claims onto the headers AI Almanac reads:
#   sub   -> X-Forwarded-User
#   email -> X-Forwarded-Email
```

```caddy
almanac.example.org {
    reverse_proxy 127.0.0.1:4180   # oauth2-proxy
}
```

If your proxy emits different header names, point AI Almanac at them with
`SUBMITTED_BY_HEADER`, `IDENTITY_EMAIL_HEADER`, and `IDENTITY_NAME_HEADER`.

### Operations

- **Health check**: `GET /health` returns `{"status": "ok"}`.
- **Backup**: `pg_dump` the database **and** archive
  `$AI_ALMANAC_DATA_DIR/jobs` + `/uploads` (artifacts live on the filesystem,
  not in PostgreSQL). Dataset mounts are read-only inputs and don't need backup.
- **Restore**: restore the database, restore the data volume, start the service.
- **Upgrade**: deploy the new version and restart — migrations apply on
  startup. Running jobs are reconciled from the database on restart.
- **Rollback**: redeploy the previous version against a database backup taken
  before the upgrade (migrations are additive; a forward-only schema may not
  match an older binary).

---

## Current limitations

The multi-user **application layer** — authentication, roles, per-user
ownership, sharing, the catalog, and artifact indexing — works on PostgreSQL.
**Job execution does not yet.** The durable job supervisor and workload
(`ai-almanac execute-job` / `run-job-workload`) read and write job state through
a local SQLite database under the data directory, independent of
`DATABASE_URL`. In a PostgreSQL deployment the API would record a job in
PostgreSQL while the supervisor looks for it in SQLite, so benchmarks submitted
in shared mode will not run.

Until the supervisor is ported to the configured database:

- Use **personal mode** to actually run benchmarks.
- Treat the **shared profile** as the target architecture for the auth,
  catalog, and review surfaces — and as the deployment shape to validate on
  staging — not as a job-running production system yet.

Object storage and remote runners (Modal, Slurm, batch services) are also
deferred; the storage and runner interfaces are designed to accept them without
changing the public product concepts.

## Configuration reference

| Variable | Mode | Default | Notes |
| --- | --- | --- | --- |
| `DEPLOYMENT_MODE` | both | `personal` | `personal` \| `shared` |
| `AUTH_MODE` | both | `none` | `none` \| `proxy`; forced `proxy` in shared |
| `DATABASE_URL` | both | SQLite | PostgreSQL required in shared |
| `AI_ALMANAC_DATA_DIR` | both | per-user dir | DB (personal) + all artifacts |
| `ADMIN_SUBJECTS` | shared | — | Comma-separated OIDC subjects |
| `ADMIN_EMAILS` | shared | — | Comma-separated emails |
| `SUBMITTED_BY_HEADER` | both | `X-Forwarded-User` | Subject header |
| `IDENTITY_EMAIL_HEADER` | shared | `X-Forwarded-Email` | |
| `IDENTITY_NAME_HEADER` | shared | `X-Forwarded-Preferred-Username` | |
| `DATASET_MOUNT_ROOTS` | shared | — | Allow-list for mounted source paths |
| `ENABLE_FS_BROWSER` | both | `true` | Forced `false` in shared |
| `ENABLE_RUN_CODE` | both | `true` | Forced `false` in shared |
| `RUNNER_MODE` | both | `pixi` | `pixi` \| `stub` |
| `MAX_LOCAL_JOBS` | both | `1` | Concurrent benchmark jobs |
| `OUTPUT_DIR` | both | (data dir)`/jobs` | Bulk output location |
| `FRONTEND_URL` | dev | `http://localhost:5173` | CORS origin for the Vite dev server |
