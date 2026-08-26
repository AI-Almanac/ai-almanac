# Local-first single-process deployment

Design plan for running ai-almanac fully locally — no cloud services, no
containers — with the install story:

```bash
uv tool install ai-almanac
ai-almanac serve
```

Target hardware is single-node GPU machines (NVIDIA DGX Spark, linux-aarch64),
but nothing here is Spark-specific. The intended operator is not necessarily
a software or systems person.

## Decisions

- **Single-user local, like JupyterLab.** Local installs are personal-mode,
  single-user, loopback-only. Multi-user (shared mode) stays cloud-only
  behind proxy auth. Multiple people on one box run per-user instances —
  own `AI_ALMANAC_DATA_DIR`, own port — with a shared env root and shared
  dataset mounts (Phase 3). Built-in password accounts are deferred until
  real demand appears; see "Deferred" for the options already evaluated.
- **Database:** SQLite for local installs (already the default); Postgres
  for the managed cloud deployment. No SQLite-in-shared work needed.
- **Onboarding:** both a web first-run wizard (default path, Jupyter-style
  one-time setup URL) and a `ai-almanac init` CLI for scripted/headless
  setups.
- **Jobs:** keep the detached supervisor + subprocess-in-pixi-env model
  unchanged. It already provides isolation and durability; "single process"
  means one process *the user starts*, not one process total.
- **LLM:** no code change to the integration — `llm.py` already builds an
  OpenAI-compatible client from `llm_base_url`/`llm_model`. The wizard
  configures it; docs cover serving Nemotron via NIM or vLLM.
- **Cloud = managed instance of the local product.** The GCP deployment
  runs the same `ai-almanac serve` in shared mode: Postgres via
  `DATABASE_URL`, GCS buckets mounted as plain paths via Cloud Run GCS
  FUSE volumes, `JOB_RUNNER=modal`. `LocalStorage` becomes the *only*
  app-side storage backend; `GCSStorage`, the gcsfs listing helpers, and
  the `/vsigs/` tile branch are deleted. gs:// survives only at the Modal
  dispatch boundary (workers can't see the mounts), as a mount-path ↔
  gs:// translation at submission time. This collapses the test matrix to
  two blessed configurations instead of a combinatorial product:

  | Config | DB | Runner | Auth | Storage |
  |---|---|---|---|---|
  | personal (local, incl. Spark) | SQLite | local | none | local paths |
  | managed cloud | Postgres | Modal | proxy | FUSE-mounted buckets |

  CI covers these two named configs; the storage code path is identical
  in both.
- **DuckDB:** rejected as the system DB (job queue/heartbeats are OLTP;
  SQLite with WAL + `BEGIN IMMEDIATE` already handles the supervisor's
  concurrent writers). May reappear later as a read-only analytics layer
  over benchmark outputs.

## Out of scope (explicitly deferred)

- **Local multi-user / built-in password accounts.** Options evaluated,
  preserved for when demand appears: (a) fastapi-users — covers backend
  auth flows but is in maintenance mode, and its ORM-based adapter fights
  our SQLAlchemy Core `users` table; (b) minimal hand-rolled — argon2 +
  sessions table + admin-managed users only (no self-registration, no
  email flows), a few hundred lines but a security surface we'd own;
  (c) a no-password "user picker" for trusted shared boxes — attribution
  without an auth boundary, safe only on loopback. Per-user instances
  cover the need until then.
- **Results export/sharing tooling** (bundles, publish-to-shared-path) —
  worth doing, scoped in a separate discussion.
- Local replacement for the Modal-only `run_code` / `run_code_sandbox` chat
  tools. They stay gated off when `job_runner != "modal"`.
- OIDC built-in client.
- Air-gapped env transport (`env pack`/`unpack`) — stretch goal, noted in
  Phase 5.
- DuckDB analytics layer.

---

## Phase 0 — spikes (do first)

Two validation spikes gate the rest of the plan:

1. **aarch64 forecast env** — see Phase 2. Highest uncertainty.
2. **GCS FUSE convergence** — on staging, mount the buckets as Cloud Run
   GCS FUSE volumes and run the app with `storage_backend=local` against
   the mount paths. Validate: rename semantics (gcsfuse rename =
   copy+delete, non-atomic — audit for write-temp-then-rename patterns;
   `storage.py` has none today), tile-serving latency through FUSE vs
   `/vsigs/` direct range reads, and listing performance on large dataset
   prefixes. If this passes, Phase 1 deletes the GCS storage code rather
   than extra-izing it.

## Phase 1 — de-cloud the wheel, converge storage

*One PR (plus a staging deploy change). Assumes the FUSE spike passed.*

- **Delete** `GCSStorage`, the gcsfs listing helpers in `storage.py`, the
  gs:// inspection branch in `services/data_sources.py`, and the `/vsigs/`
  branch in `routers/tiles.py`. `LocalStorage` is the only storage
  backend; cloud mounts buckets via GCS FUSE.
- Replace the shared-mode "user datasets must be gs:// URLs" rule in
  `routers/data_sources.py` with "must be under `dataset_mount_roots`" —
  the same invariant local shared mode already uses.
- Modal boundary keeps gs://: add a mount-path ↔ gs:// URI translation at
  submission time (`modal_runner.py` already accepts absolute paths for
  mounted volumes, so the shape exists). Modal workers still write
  outputs via the GCS API; the app reads them back through the mount.
- Move `modal`, `psycopg[binary]`, and `globus-sdk` to a `cloud` extra
  (`ai-almanac[cloud]`) with lazy imports at their existing seams
  (`runner_registry.py`, `db.py`/`sync_db.py` URL-scheme branch,
  `auth.py` globus path). `google-cloud-storage`/`gcsfs` remain only if
  the Modal translation layer needs them; otherwise dropped entirely.
- Terraform/deploy: add the FUSE volume mounts, set storage paths.

CI: add a job that installs the bare wheel and runs the API test suite to
keep the lazy-import boundary honest. Cloud deploy images install
`.[cloud]`. CI covers the two blessed configs from the decision table.

Result: `uv tool install ai-almanac` pulls no cloud SDKs, and the storage
code path is identical local and cloud.

## Phase 2 — pixi auto-bootstrap + aarch64 validation

*One PR plus the Phase-0 hardware spike.*

- `envs/manager.py`: replace `_require_pixi` with `_ensure_pixi()` —
  use `pixi` from PATH if present, otherwise download a version-pinned
  static binary for the current platform into
  `$AI_ALMANAC_DATA_DIR/bin/pixi`, verify its sha256 against pins committed
  in the repo. This removes the last manual prerequisite.
- Refactor `ensure_env()` to accept a progress callback and capture
  subprocess output line-by-line (instead of inheriting stdout). The CLI
  keeps its current behavior; the wizard (Phase 4) streams the same events.
- **Spike detail (Phase 0 item 1):** on a DGX Spark, run
  `ai-almanac env prepare` and verify the `forecast` env solves and runs on
  `linux-aarch64` with the CUDA/sbsa pins — including the `aifs2` /
  `aifs2ens` per-family solves and an end-to-end `earth2studio` inference.
  Also verify model-weight downloads (ECMWF/NGC auth, multi-GB pulls).
  Fix pins as needed; this is the part that can surprise us.

## Phase 3 — serve UX, secrets bootstrap, multi-instance ergonomics

*One small PR.*

- **Secrets auto-generation:** on first run, generate
  `credential_encryption_key` and `chat_figure_signing_secret` and write
  them to `$AI_ALMANAC_DATA_DIR/secrets.env` (mode 0600). Precedence: env
  var > secrets file. Kills the chicken-and-egg where `PATCH /settings`
  on any secret 400s until a key exists, so the wizard can seal LLM API
  keys without manual key management.
- **Shared env root:** new `AI_ALMANAC_ENV_ROOT` (default: data dir) so
  per-user instances on one box share the multi-GB pixi envs instead of
  each installing their own copy.
- **Optional access token:** `serve --token` (or setting) requires a
  Jupyter-style token on first browser contact — loopback is not a
  boundary between OS users on a shared box. Off by default on
  single-user desktops; documented for shared machines.
- `serve` stays loopback-only in personal mode; startup prints the URL and
  docs cover SSH tunneling / Tailscale for remote access.

## Phase 4 — onboarding wizard (web + CLI)

*Two PRs: backend then frontend.*

**Backend (`routers/setup.py` + install-state):**

- Install state = `setup_complete` flag in the `app_config` overlay.
  When unset, `serve` generates a one-time bootstrap token, prints
  `http://127.0.0.1:8765/setup?token=…`, and a lightweight middleware
  gates everything except `/setup`, `/api/setup/*`, and static assets.
- `/api/setup/*` endpoints (bootstrap-token-auth'd):
  - `GET state` — detected platform, GPU (`nvidia-smi` probe), data dir,
    env readiness per env.
  - `POST storage` — data dir confirmation, `dataset_mount_roots`.
  - `POST llm` — base URL + model + optional key; server-side test button
    probes `{base_url}/models` and runs a one-token completion.
  - `POST envs/prepare` + `GET envs/events` (SSE) — background task
    wrapping the Phase-2 `ensure_env(progress_cb)`; GPU probe decides
    whether the forecast env is offered.
  - `POST finish` — persist overlay, mark complete. No restart needed:
    with mode fixed at `personal`, nothing the wizard touches is in
    `RESTART_REQUIRED_FIELDS`.
- Extend `/ready` to report env-prepared status so the UI can surface
  "forecast env missing" outside the wizard too.

**Frontend:** new top-level `/setup` route (the SPA nav-fallback in
`app.py` already serves unknown document GETs). Linear steps mirroring the
endpoints above, with the env-prepare step streaming log lines and
per-env progress. Reuse the schema-driven form components from
`web/src/routes/settings/`. Wizard must be idempotent and re-enterable
(abandoned midway, browser closed during env prepare).

**CLI:** `ai-almanac init` — the same steps as typer prompts
(data dir, LLM endpoint, env prepare), writing `config.yaml`; `--yes`
plus flags for headless provisioning. Shares the same service-layer
functions as the setup router so the two paths cannot drift.

## Phase 5 — distribution polish

*One PR, mostly docs and scripts.*

- README/docs rewrite around `uv tool install ai-almanac` →
  `ai-almanac serve` → browser wizard. Keep `pipx` as the alternative.
- A curl-able `install.sh`: installs uv if missing, `uv tool install
  ai-almanac`, launches `serve`.
- **DGX Spark / Nemotron guide:** serving Nemotron via NVIDIA NIM or vLLM
  (both expose OpenAI-compatible endpoints), pointing the wizard's LLM step
  at it, VRAM budgeting between the LLM and forecast jobs
  (`max_local_jobs` guidance).
- **Shared-box guide:** per-user instances (data dir + port), shared
  `AI_ALMANAC_ENV_ROOT`, shared `dataset_mount_roots`, access token.
- Mirror/publish `ROMP` and `onset_blending-adm3` (PyPI or tarball
  mirrors) so `env prepare` doesn't depend on personal GitHub repos
  staying put — cheap supply-chain insurance for distributed installs.
- `ai-almanac install-service` — emit/enable a systemd unit for
  always-on lab boxes (nice-to-have).
- Stretch: `ai-almanac env pack/unpack` for air-gapped installs (pixi envs
  relocate acceptably when the data-dir path is kept consistent).

---

## Sequencing and risk

```
Phase 0 spikes (aarch64 forecast env, GCS FUSE)  ← do first
Phase 1  →  Phase 2 PR  →  Phase 3  →  Phase 4a → 4b  →  Phase 5
```

All phases are now small-to-medium PRs; the former built-in-auth phase
(previously the largest and riskiest) is deferred with local installs
being single-user. Phase 4 depends on 2 (progress callback) and 3
(secrets bootstrap).

Other risks and mitigations:

- **Migrations:** all schema changes are additive (rollback-safe one
  version, per repo policy).
- **SQLite environment hazards:** warn or refuse at startup when the data
  dir is on a network filesystem (NFS + SQLite = corruption); add an
  `ai-almanac backup` command early.
- **Stale copy:** the settings overview page claims changes are "saved to
  config.yaml" — they land in the DB overlay. Fix while in there (Phase 4b).
- **GPU contention:** local LLM + forecast inference share the GPU. The
  GB10 in DGX Spark does not support MIG (single desktop SoC GPU — no
  SXM/Fabric Manager/vGPU), so hard partitioning is off the table. Plain
  CUDA time-slicing lets the LLM server and forecast subprocesses run
  concurrently with no changes, and the 128 GB unified memory means both
  can co-reside — the constraint is compute/bandwidth, not memory
  partitioning. Document `max_local_jobs=1` as the default posture on
  Spark (per-user instances multiply this — note it in the shared-box
  guide); if LLM latency under contention bites, enable CUDA MPS before
  reaching for anything fancier.
