# Multi-User Deployment Readiness Plan

Status: proposed. This is the concrete, codebase-grounded implementation plan
derived from the high-level readiness proposal, after auditing the current
code. It corrects three assumptions in the original proposal that do not match
what is already in the repo, then lays out a phased plan with named files,
schema deltas, and acceptance tests.

## Goal

Preserve the zero-config personal-device experience (SQLite, no auth, local
filesystem, local process execution) while adding a secure **shared mode**
(PostgreSQL, proxy OIDC, admin/user roles) behind clean execution and storage
boundaries that can later grow to object storage and remote runners without
changing product concepts.

Modes:

- **Personal** (default): SQLite, no auth, local filesystem, detached
  local-process execution, implicit `local` user.
- **Shared**: PostgreSQL, `AUTH_MODE=proxy` with trusted identity headers,
  `admin`/`user` roles, filesystem artifacts, same local-process execution.

Reference shared auth: Globus OIDC through Caddy + oauth2-proxy.

---

## Findings From the Current Code (corrections to the original proposal)

The proposal was written as if execution and identity were greenfield. They are
not. Three corrections drive the real work:

### 1. The detached supervisor already exists — do not rebuild it as a "state file"

`src/ai_almanac/server/services/job_manager.py` already implements durable,
crash-recoverable local execution:

- `launch_job()` spawns a detached supervisor: `ai-almanac execute-job <id>`.
- `execute_job()` registers the supervisor, claims a concurrency slot
  (`max_local_jobs`), and spawns the workload: `ai-almanac run-job-workload <id>`.
- Lifecycle state — `worker_pid`, `workload_pid`, `process_group_id`,
  `heartbeat_at`, `cancel_requested_at`, `exit_code` — lives in the `jobs`
  table (migration `0004_durable_job_execution`).
- `reconcile_jobs()` runs on startup and on a 5s loop in `app.py`, recovering
  queued jobs and finalizing supervisors whose heartbeat went stale.

**The proposal's "write an atomic workspace state file containing status, PID,
timestamps, exit code" would regress this.** The DB is already the single source
of truth and it works across API restarts. Keep DB-backed reconciliation for the
local runner. A workspace state file only becomes useful for *remote* runners
where the app can't `os.kill(pid, 0)` the process — defer it with those.

So the runner work is **not** "extract the supervisor"; it is **formalize the
existing supervisor behind a `JobRunner` Protocol and delete the dead code** (see
finding 2).

### 2. `services/runner.py` is mostly dead code

Tracing call sites: the live execution path is
`job_manager.execute_job` → `job_workload.run_job_workload`. The workload only
reuses two *static helpers* from `runner.py`:

- `InProcessRunner._job_env(...)` (env construction), and
- `StubRunner._write_metric_nc / _resolve_grid / _write_placeholder_figure / WINDOWS`.

Everything else in `runner.py` — the threaded `run_job`/`_execute` orchestration,
`_update_status`, the `_semaphore`, `get_runner`/`reset_runner` — is **no longer
called by the job pipeline**. The "remove or consolidate the overlapping legacy
in-process runner" step is therefore concrete and low-risk: lift the surviving
helpers into a bundle-compiler module and delete the rest. Confirm with a
grep for `get_runner`/`reset_runner`/`.run_job(` before deleting; a couple of
tests may still import them.

### 3. The sync registry path silently breaks on PostgreSQL — this is the #1 blocker

`settings.py::_sync_db_query()` opens a **raw `sqlite3` connection** and returns
`[]` for any non-SQLite URL:

```python
if not url.drivername.startswith("sqlite"):
    return []  # Postgres returns nothing
```

`get_model_registry()`, `get_regions()`, and `get_demo_datasets()` all depend on
it. On PostgreSQL (i.e. shared mode) **the model list, region list, and dataset
list silently become empty** — benchmarks cannot be configured at all. The
original proposal never mentions this. It must be fixed before shared mode can
function, and it is the riskiest single change because these sync resolvers are
called from request handlers and from `romp` config generation.

Resolution options (decide in Phase 4, lean toward A):

- **A. Async-ify the registries.** Add async `data_sources`/`regions` service
  reads and call them from the routers; keep a thin sync shim only where a sync
  caller truly can't be reached (audit `romp.py`, `chat_tools.py`).
- **B. Driver-agnostic sync read.** Replace the `sqlite3` shim with a short-lived
  synchronous SQLAlchemy engine (psycopg sync) for the registry queries.

Option A is cleaner long-term and removes the parallel read path; Option B is a
smaller diff. Recommend A, with B as a fallback if async-ifying `romp` config
generation proves invasive.

### Other confirmed facts the plan relies on

- `DATABASE_URL` override is already wired (`db.py::_make_engine`,
  `settings.resolve_database_url`). Postgres engine path exists; only the sync
  registry read is broken.
- The WebSocket `GET /jobs/{id}/stream` takes **no `CurrentUser`** and queries
  jobs by id only — anyone can stream anyone's logs. Real vuln; Phase 2 fixes it.
- `attribution.current_user` calls `get_or_create_user` for *any* header value,
  defaulting to `local`. In shared mode this auto-provisions a user for any
  spoofed header and never assigns roles.
- HTTP job/dataset routes already filter by `user_id`; `data_sources` rows have
  **no owner/visibility columns** (migration `0002`), so every source is global.
- `settings` router has `SENSITIVE_FIELDS` masking but **no admin gating** — any
  caller can `PATCH /settings` and `?reveal=true` secrets.
- CI: only `.github/workflows/release.yml` exists. No test/lint gate.
- `pixi run lint-python` currently reports **43 ruff errors** (Phase 1).
- Migration chain is a clean additive line `0001 → 0005`; new work appends
  `0006+`.

---

## Configuration Surface

Add to `Settings` (`src/ai_almanac/settings.py`):

| Setting | Default | Notes |
| --- | --- | --- |
| `deployment_mode` | `personal` | `personal` \| `shared`. |
| `auth_mode` | `none` | `none` \| `proxy`. Forced `proxy` in shared. |
| `admin_subjects` | `""` | Comma-separated OIDC subjects granted `admin`. |
| `admin_emails` | `""` | Comma-separated emails granted `admin`. |
| `identity_subject_header` | `X-Forwarded-User` | Reuse existing `submitted_by_header` semantics. |
| `identity_email_header` | `X-Forwarded-Email` | |
| `identity_name_header` | `X-Forwarded-Preferred-Username` | |

Shared-mode **startup invariants** (fail fast in `lifespan`):

- `database_url` must be PostgreSQL (reject SQLite).
- `auth_mode == "proxy"`.
- At least one of `admin_subjects` / `admin_emails` is non-empty.
- `enable_fs_browser` and `enable_run_code` default to **False** and a warning
  is logged if a config tries to enable them.

Add these to `RESTART_REQUIRED_FIELDS`. Most are admin/CI-only (env), never
surfaced as editable in the Settings UI.

---

## Identity and Authorization

Replace the attribution dict with a parsed identity (`parse, don't validate`).

`src/ai_almanac/server/auth.py` (new):

```python
@dataclass(frozen=True)
class CurrentUser:
    id: str            # internal users.id (uuid)
    subject: str       # stable OIDC sub / "local"
    email: str | None
    display_name: str | None
    role: Literal["admin", "user"]

async def current_user(request: Request) -> CurrentUser: ...
```

Rules:

- **Personal/`auth_mode=none`**: implicit `subject="local"`, `role="admin"`
  (the local operator owns the box). No headers required.
- **Shared/`auth_mode=proxy`**: require the subject header; `401` if missing
  (blocks direct-to-app bypass of the proxy). Role is `admin` iff subject or
  email is in the configured admin lists, else `user`. Persist
  `subject/email/display_name` via an extended `get_or_create_user`.

Reusable dependencies (in `auth.py`):

- `require_user` — any authenticated user.
- `require_admin` — `403` unless `role == "admin"`.
- `require_owner_or_admin(resource_owner_id)` — factory for row-scoped checks.
- `authorize_read(visibility, owner_id, user)` — owner, admin, or `shared`.
- A WebSocket variant `current_user_ws(websocket)` (headers still arrive on the
  WS handshake) so `stream_job` can authorize.

`GET /auth/me` (new router) returns identity + capabilities
(`can_admin`, `can_browse_fs`, `can_run_code`, `deployment_mode`) for the
frontend to drive nav and controls.

Apply gating:

- `require_admin` → all of `settings` router, `?reveal=true`, region mutation
  (`regions` POST/PATCH/DELETE), mounted-source registration, global catalog
  management, `fs` browser router.
- Owner/visibility checks → jobs, datasets, chat, data sources, artifacts, and
  the job WebSocket.

---

## Data Model (additive migrations `0006`, `0007`)

`0006_ownership_and_visibility`:

- `data_sources`: add
  `owner_id TEXT NULL` (NULL = built-in/operator-global),
  `visibility TEXT NOT NULL DEFAULT 'shared'` (`private` \| `shared`),
  `location_type TEXT NOT NULL DEFAULT 'mounted'` (`mounted` \| `upload`).
  Built-in/seeded and admin-registered mounted sources are `shared`. User
  uploads are `upload` + `private` by default; owner may share/unshare.
- `jobs`: add `visibility TEXT NOT NULL DEFAULT 'private'`,
  `runner TEXT NULL`, `runner_handle JSON NULL`.
- `users`: add `display_name TEXT NULL` (email already exists).

`0007_job_artifacts`:

```sql
job_artifacts(
  id TEXT PRIMARY KEY,
  job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- 'metric' | 'figure' | 'log' | 'output'
  filename TEXT NOT NULL,
  media_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  checksum TEXT NOT NULL,        -- sha256
  storage_key TEXT NOT NULL,     -- opaque; no filesystem assumptions leaked
  created_at TEXT NOT NULL
)
```

Visibility policy: jobs and their artifacts share one visibility value. Sharing
is **read-only** — it never grants cancel, delete, or rerun. Admins manage all
rows. Keep all migrations additive; never rewrite `0001`–`0005`.

---

## Storage Boundaries

`src/ai_almanac/server/services/datasets_resolver.py` and
`artifact_store.py` (new), Protocols as in the proposal:

```python
class DatasetResolver(Protocol):
    async def resolve(self, source: DataSource, workspace: Path) -> ResolvedDataset: ...

class ArtifactStore(Protocol):
    def create_workspace(self, job_id: str) -> Path: ...
    def publish(self, job_id: str, workspace: Path) -> list[JobArtifact]: ...
    def open(self, artifact: JobArtifact) -> BinaryIO: ...
    def delete_job(self, job_id: str) -> None: ...
```

`FilesystemArtifactStore` wraps the existing `LocalStorage`
(`services/storage.py`) — most methods already exist (`job_output_uri`,
`list_result_files`, `result_file_path`, `read_log`). `publish()` is the new
piece: after a successful job it walks the workspace, computes sha256 + size +
media type, and writes `job_artifacts` rows. `storage_key` stays opaque so the
routers/runners never see filesystem paths. Keep S3 out of v1, but ensure no
router branches on `storage.is_local` for the new artifact path (the old
`is_local`/signed-URL branch in `jobs.py` can stay until S3 lands).

`FilesystemDatasetResolver`:

- `mounted` sources: **canonical-path containment check** — resolve the source
  path and assert it is within a configured allow-list root; `403` on traversal.
- `upload` sources: resolve under the per-user uploads dir.

---

## Runner Architecture

Provider-neutral types in `src/ai_almanac/server/services/execution.py` (new):

```python
class JobRunner(Protocol):
    name: str
    capabilities: RunnerCapabilities
    async def submit(self, request: ExecutionRequest) -> RunnerHandle: ...
    async def inspect(self, handle: RunnerHandle) -> ExecutionSnapshot: ...
    async def cancel(self, handle: RunnerHandle) -> None: ...

@dataclass(frozen=True)
class ExecutionRequest:
    job_id: str
    bundle_path: Path
    workspace: Path
    inputs: tuple[ResolvedDataset, ...]
    resources: ResourceRequest

@dataclass(frozen=True)
class RunnerHandle:
    runner: str
    external_id: str
    metadata: Mapping[str, JSONValue]
```

Plan:

1. **Bundle compiler** (`services/bundle.py`, new): move ROMP config generation
   (`romp.py::write_romp_config`) and the surviving `InProcessRunner._job_env`
   helper here. Compile validated job config → an execution bundle on disk
   *before* submission. ROMP-specific config stays out of the runner.
2. **`LocalProcessRunner`** wraps the existing `job_manager` supervisor:
   - `submit` = today's `launch_job`; returns a `RunnerHandle`
     (`external_id` = supervisor/workload pid set, `metadata` = pgid).
   - `inspect` = read DB lifecycle columns / `reconcile_jobs` logic →
     `ExecutionSnapshot`.
   - `cancel` = today's `request_cancel` + `_terminate_process_group`.
   - Persist the handle in `jobs.runner` / `jobs.runner_handle`.
   - **Keep DB-backed reconciliation** (finding 1). No workspace state file.
3. **Delete dead code** in `services/runner.py` (finding 2); fix the
   `job_workload.py` imports to point at `bundle.py`.
4. **Boundaries**: runners never write app DB business rows, authorize users,
   interpret results, or decide retention. (Today's `execute_job` *does* write
   `jobs` status/pid columns — that is lifecycle bookkeeping the supervisor owns,
   which is acceptable; keep business-state writes — visibility, artifacts — out
   of it.)
5. **Defer** Modal / Slurm / AWS Batch / Google Batch / plugin discovery until
   the local contract is proven and `inspect`/state-file semantics are needed.

---

## Reference Deployments (`docs/deploy/`)

1. **Personal / NVIDIA device**: native pixi install, SQLite, local FS, direct
   GPU, single `ai-almanac serve`, optional `systemd` unit + Caddy TLS.
2. **Shared self-hosted**: PostgreSQL, Caddy + oauth2-proxy (Globus OIDC), app
   bound to the private interface only, persistent artifact/upload volume,
   read-only dataset mounts, configured admin subjects/emails, GPU app process
   using `LocalProcessRunner`. Include backup/restore, health check, upgrade,
   and rollback runbooks. The public site adopts this profile — it does **not**
   reproduce the old Cloud Run / GCS / Modal architecture.

---

## Implementation Order

1. **Green baseline + CI.** Fix the 43 ruff errors (`pixi run lint-python
   --fix` covers ~30; hand-fix the rest) and the Svelte `check-web` failures.
   Add a `.github/workflows/ci.yml` running `pixi run check` + `pixi run test`
   + `pixi run check-web` on PRs. *Gate everything after this on green CI.*
2. **Identity + policy.** `deployment_mode`/`auth_mode`/admin config, parsed
   `CurrentUser`, `require_user`/`require_admin`/owner deps, `/auth/me`,
   **WebSocket authorization**, shared-mode startup invariants. Personal mode
   behavior unchanged.
3. **Postgres registry fix (finding 3) + ownership migrations.** Async-ify the
   model/region/dataset registries so they work on Postgres; add `0006`/`0007`;
   secure every settings/catalog/job/chat route with owner/admin/visibility.
4. **Storage boundaries.** `DatasetResolver`, `ArtifactStore`, filesystem impls,
   canonical-path containment for mounted sources.
5. **Runner contract.** `JobRunner`, bundle compiler, persisted handles,
   `LocalProcessRunner` over the existing supervisor; delete dead `runner.py`.
6. **Artifacts.** Publication + `job_artifacts` indexing on success, download
   authorization, sharing, deletion cascade.
7. **Frontend.** Account state from `/auth/me`, sharing controls, admin-only nav
   and actions.
8. **Deploy profiles.** Personal `systemd`; shared Caddy + oauth2-proxy +
   PostgreSQL examples and runbooks.
9. **Staging.** Fresh shared instance; migrate representative data; full E2E.
10. **Cutover.** Replace the public deployment only after staging passes
    recovery, cancellation, sharing, and restart tests.

Sequencing note: 2 and 3 both touch request auth; do 2 first so 3's route
hardening builds on real identities. 1 is a hard prerequisite for all of it.

---

## Test Plan

- **Personal mode** retains implicit `local` identity, admin-equivalent
  capability, and SQLite behavior with no headers.
- **Shared mode** refuses SQLite at startup, `401`s on missing identity headers,
  rejects spoofed direct access, and refuses to start with no admins configured.
- Users cannot read another user's private jobs, chats, uploads, sources, logs,
  artifacts, **or WebSocket streams**.
- `shared` records are readable but mutable only by owner/admin; sharing never
  grants cancel/delete/rerun.
- Only admins reveal/change secrets, register mounted paths, mutate regions, or
  change global settings.
- Canonical-path checks block mounted-source traversal outside allowed roots.
- **Postgres registry regression test**: model/region/dataset lists are
  non-empty on a Postgres-backed app (guards finding 3 permanently).
- Local jobs survive API restarts, reconcile from DB state, respect
  `max_local_jobs`, and cancel cleanly.
- Successful jobs atomically publish indexed artifacts; failed jobs keep logs
  without publishing partial outputs.
- Deleting a job removes its artifact rows + files, leaves datasets intact.
- Postgres integration tests cover concurrent submission and ownership queries.
- E2E: Globus login/logout, admin access, private upload, share/unshare,
  benchmark run, restart recovery, artifact download.

---

## Assumptions

- The existing public Cloud Run infra and its DB are **not** compatibility
  requirements; staging starts fresh.
- First shared deployment is a single execution host with locally mounted GPU
  and storage.
- PostgreSQL is mandatory only in shared mode.
- Object storage and remote runners are deferred, but the `ArtifactStore` /
  `JobRunner` contracts must support them without changing product concepts.
- DB-backed reconciliation is retained for `LocalProcessRunner`; the
  proposal's workspace state file is deferred to remote runners that need it.
```
