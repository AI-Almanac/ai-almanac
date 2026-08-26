# Phase 1 spec — de-cloud the wheel, converge storage

**One PR** (plus a staging deploy change and a Modal app redeploy).
Implements Phase 1 of `docs/local-first-single-process.md`. Assumes the
Phase-0 GCS FUSE spike passed. Companion specs:
`docs/local-first/phase-2-pixi-bootstrap.md`, `phase-3-serve-ux.md`,
`phase-4-onboarding-wizard.md` (no overlap; Phase 4's
`POST /api/setup/storage` writes `dataset_mount_roots`, which this phase
makes the universal containment rule).

## Current state (verified)

- `src/ai_almanac/server/services/storage.py` — `LocalStorage` (job layout
  `{outputs_dir}/{job_id}/output|figure`, `{job_id}/run.log`,
  `chat-figures/{id}.{ext}`) and `GCSStorage` (identical object layout).
  **The two layouts already match key-for-key** — mounting the outputs
  bucket at `output_dir` makes `LocalStorage` read Modal-written outputs
  with zero key rewriting. `get_storage()` branches on
  `settings.storage_backend`.
- `LocalStorage.result_file_path` rejects nested filenames and
  `list_result_files` is **non-recursive** (`iterdir`), while
  `GCSStorage.list_result_files` lists nested blobs. Forecast jobs produce
  nested outputs (`{model_id}/manifest.json`,
  `{model_id}/rasters/{var}/{lead}.tif`), so cloud artifact indexing
  currently depends on the GCS recursive listing. Also `routers/jobs.py:348`
  (`filename:path`) falls into `assert isinstance(storage, GCSStorage)` for
  nested names — LocalStorage must gain nested-path support or nested
  downloads 500 in the converged world.
- `services/artifact_store.py` — `GcsArtifactStore`, `get_artifact_store`
  isinstance branch.
- `services/data_sources.py` — `_inspect_gcs_source`, gs:// branch in
  `validate_source`, `location_type = "gcs" if path.startswith("gs://")`.
- `routers/data_sources.py` — `_normalized_path` gs:// passthrough,
  `_check_path_allowed` "user datasets must be gs:// URLs".
- `routers/tiles.py` — no gs:// code of its own; `/vsigs/` came from
  `GCSStorage.result_file_uri`. Only docstrings change.
- `services/job_submission.py` — `"gcs_cache_bucket":
  settings.gcs_data_bucket` in blend config (line 644) and forecast config
  (line 956); `season_store_prefix = gs://{gcs_data_bucket}/season-forecasts
  | "season-forecasts"` (persisted as `trajectory_sets.storage_prefix`);
  `storage.is_local` workspace branch (line 1249); `blend_output_uri =
  get_storage().job_output_uri(blend_id)[0]` (line 823) — becomes a plain
  path, consumed by Modal (`modal/blending_app.py:2332` requires gs://) and
  locally (`envs/forecast_entrypoint.py:84` branches on gs://).
- `services/modal_runner.py` — preflight requires gs:// obs/model URIs and a
  non-empty `outputs_bucket` (from `settings.gcs_outputs_bucket`); `_spawn`
  passes `(job_id, config, outputs_bucket)`. `modal` import already lazy.
- `services/job_workload.py` — `is_local` guards (lines 72, 116); local
  blend cache injected as `cache_dir()/blend-intermediates`; local forecast
  cache is hardcoded in `envs/forecast_entrypoint.py:58,99` — **local cache
  paths never come from config**, which simplifies the design.
- `services/benchmark_domain.py:1124` — `_exec_run_code` reaches into
  `storage._outputs_bucket` (breaks when GCSStorage dies).
- `services/job_manager.py:141` — `_modal_failure_log` reads the run log via
  `get_storage().read_log` (works unchanged through the mount).
- `server/app.py:267` — `_storage_ready` gcs branch.
- `server/auth.py:387` — mount-roots invariant skipped when
  `storage_backend != "local"`. `auth.py:134,167` — `globus_sdk` imports
  already lazy. `db.py`/`sync_db.py` never import psycopg directly —
  SQLAlchemy lazy-loads it from the drivername. The lazy-import seams
  already exist; extra-ization is almost purely packaging.
- `settings.py` — `storage_backend`, `gcs_uploads_bucket`,
  `gcs_outputs_bucket`, `gcs_data_bucket`. `Settings` has `extra="ignore"`
  and `_apply_overlay` skips keys not in `model_fields` — removed fields
  degrade gracefully for existing config.yaml/DB overlays and env vars.
- `GCSStorage._uploads_bucket` is **write-only** (never read) — the uploads
  bucket is dead code in cloud today.
- `pyproject.toml:31` — `modal`, `psycopg[binary]`, `globus-sdk`,
  `google-cloud-storage`, `gcsfs` all in core deps. `Dockerfile:25` builds
  the plain wheel.
- `terraform/modules/almanac-env/main.tf` — sets `STORAGE_BACKEND=gcs`,
  `GCS_*`, `CPL_MACHINE_IS_GCE`; no GCS FUSE volumes exist yet.
- `compose.local-gcs.yaml`, pixi tasks `self-host-local-gcs` /
  `self-host-local-modal`, `scripts/self-host-local.sh --storage gcs`.
- `modal/app.py`, `modal/blending_app.py`, `modal/forecasts_app.py` — read
  `obs_dir`/`model_dir`/`model_files`/`blend_output_uri` as gs:// URIs,
  derive caches from `config["gcs_cache_bucket"]`, write outputs to
  `gs://{outputs_bucket}/{job_id}/...` via the GCS API. Stay gs://-native.
- `services/forecast_pipeline.py` — `_gcs_blob`/`_split_gs_uri`/gs:// branch
  of `cached_trajectory` are **worker-side** code (Modal image / forecast
  pixi env install `google-cloud-storage` themselves). The server only
  imports `season_covered_dates` from it. No change — the sanctioned gs://
  survivor alongside `modal/*`.

## Design decisions

1. **`bucket_mounts` setting** — `bucket_mounts: dict[str, str] = {}`,
   mapping absolute mount path → `gs://bucket[/prefix]`. Env form is JSON
   (`BUCKET_MOUNTS='{"/mnt/outputs":"gs://almanac-job-outputs-ai-almanac",
   "/mnt/data":"gs://almanac-data-ai-almanac"}'`); config.yaml form a plain
   mapping. In `SHARED_ENV_ONLY_FIELDS` (env-managed in cloud, meaningless
   locally, never in the settings UI).
2. **Translation happens in `modal_runner.submit`, not `job_submission`.**
   Persisted `jobs.config_json` stays in mount-path form — identical across
   the two blessed configs, readable by UI/rerun paths, re-translated with
   *current* settings on every dispatch. `_spawn` gets a deep-copied,
   translated config. New module
   `src/ai_almanac/server/services/bucket_mounts.py` owns the mapping.
3. **Outputs bucket is derived, not configured.** `outputs_bucket_name()` =
   the bucket of `to_gs_uri(settings.job_outputs_dir)`. Constraint: the
   outputs mapping must be a **bare bucket** (no key prefix) because Modal
   apps join `{job_id}/output/...` onto the bucket root. Enforced in
   `enforce_deployment_invariants` when `job_runner == "modal"` (fail at
   startup, not first submit).
4. **Translated keys** (recursively into `blend_config_snapshot`):
   `obs_dir`, `model_dir`, `blend_output_uri`, every URI in `model_files`.
   Already-gs:// values (legacy rows) pass through; values under no mount
   stay unchanged and are caught by preflight with an updated message
   ("must be a gs:// URI or a path under bucket_mounts").
5. **Caches**: new setting `shared_cache_dir: str = ""` (cloud:
   `/mnt/data/cache`). `job_submission` stops writing `gcs_cache_bucket`;
   `season_store_prefix` becomes the plain path
   `f"{shared_cache_dir or paths.cache_dir()}/season-forecasts"`. At
   dispatch, translation injects `cache_uri` (blend →
   `.../blend-intermediates`) and `trajectory_cache_uri` (forecast →
   `.../season-forecasts`) as gs:// URIs when `shared_cache_dir` maps;
   omits them otherwise (cache off, deterministic). `modal/blending_app.py`
   and `modal/forecasts_app.py` prefer the new keys and **fall back to
   `gcs_cache_bucket` for one release** so in-flight jobs finish. Local
   runners untouched (`forecast_entrypoint.py` derives local caches itself).
6. **Modal deploy ordering**: redeploy the three Modal apps (with fallback
   keys) *before* the server revision — fallback makes either order safe,
   but this order never runs a translated config against an old app.
7. **`cloud` extra** = `modal`, `psycopg[binary]`, `globus-sdk`.
   `google-cloud-storage` and `gcsfs` dropped from app deps entirely (the
   server never touches the GCS API after this PR; workers install their
   own). Dev pixi env installs `extras=["cloud"]`; new CI job installs the
   bare wheel.
8. **Back-compat for removed settings**: env/overlay keys already silently
   ignored (verified). Add a one-release deprecation warning in
   `reload_settings()` when `storage_backend`/`gcs_*` appear in the yaml/DB
   overlay, pointing at `bucket_mounts`/FUSE. No migration; no overlay rows
   deleted.
9. **Data sources**: keep the `location_type` column and API literal
   (`"gcs"`) so pre-existing rows still serialize; new rows always write
   `local_directory`. Existing cloud rows with gs:// paths are an ops task
   (re-path to mount equivalents; one-off SQL in the deploy runbook, not a
   migration — the mapping is env-specific).
10. **Blessed-config cleanup**: delete `compose.local-gcs.yaml` and
    `compose.local-modal.yaml` plus their pixi tasks and the `--storage`
    flag in `scripts/self-host-local.sh` (Modal-against-local-compose can't
    see FUSE mounts; staging is where Modal is validated).

## Ordered implementation steps

**Step 1 — `bucket_mounts` module + settings.**
- New `src/ai_almanac/server/services/bucket_mounts.py`: `parsed_mounts()`
  (resolved `Path` → normalized `gs://` prefix, longest-path-first),
  `to_gs_uri(value) -> str | None`, `outputs_bucket_name() -> str | None`,
  `translate_job_config(config) -> dict` (deep copy; keys per decisions
  4–5).
- `settings.py`: add `bucket_mounts: dict[str, str] = {}` and
  `shared_cache_dir: str = ""`; both in `SHARED_ENV_ONLY_FIELDS`.

**Step 2 — Modal boundary.**
- `modal_runner.py`: `submit` translates before preflight;
  `ModalRunner.__init__` drops `outputs_bucket` param, `get_modal_runner()`
  uses `outputs_bucket_name()`; preflight messages name `bucket_mounts`.
- `benchmark_domain.py`: `_exec_run_code` uses `outputs_bucket_name()`;
  `tool_unavailable_reason("run_code")` also returns a reason when no
  outputs bucket is mapped.
- `job_submission.py`: delete both `gcs_cache_bucket` keys;
  `season_store_prefix` per decision 5; line 1249 loses the `is_local`
  conditional (workspace is always local now).
- `modal/blending_app.py` + `modal/forecasts_app.py`: prefer
  `cache_uri`/`trajectory_cache_uri`, fall back to `gcs_cache_bucket`
  (comment: remove next release).

**Step 3 — delete GCS storage.**
- `storage.py`: delete `GCSStorage`, gcsfs helpers, the factory's gcs
  branch; `StorageBackend = LocalStorage`. Extend
  `LocalStorage.result_file_path` to accept nested relative filenames via a
  containment check (return `None` on escape); make `list_result_files`
  recursive (`rglob`, POSIX-relative names); delete dead
  `chat_figure_redirect_url`.
- `artifact_store.py`: delete `GcsArtifactStore` + isinstance branch.
- `routers/jobs.py`: drop `GCSStorage` import and the streaming branch of
  `get_result_file` — nested names now resolve to a `FileResponse`.
- `server/app.py`: `_storage_ready` local branch only.
- `routers/tiles.py`, `job_manager._modal_failure_log`: docstring/comment
  updates only.

**Step 4 — data-source rule.**
- `data_sources.py` (service): delete `_inspect_gcs_source`;
  `validate_source` always local-inspects (non-`local` providers still
  short-circuit); `location_type` always `local_directory`.
- `dataset_resolver.py`: export `mount_roots()` and public
  `is_within(path, roots)` (rename of `_mount_roots`/`_assert_within`).
- `routers/data_sources.py`: `_normalized_path` always resolves;
  `_check_path_allowed` → non-admin in shared mode must be under
  `mount_roots()` (400: "user datasets must be under the configured dataset
  mount roots").
- `auth.py:387`: drop the `storage_backend == "local"` condition (shared
  always requires `DATASET_MOUNT_ROOTS`); add the `job_runner == "modal"` ⇒
  mapped-bare-outputs-bucket invariant (decision 3).

**Step 5 — settings removal + deprecation warning.** Remove
`storage_backend`, `gcs_uploads_bucket`, `gcs_outputs_bucket`,
`gcs_data_bucket`; add the overlay deprecation warning in
`reload_settings()`.

**Step 6 — packaging.**
- `pyproject.toml`: core deps lose `modal`, `psycopg[binary]`,
  `globus-sdk`, `google-cloud-storage`, `gcsfs`; add
  `[project.optional-dependencies] cloud = ["modal>=1.5.0,<2",
  "psycopg[binary]>=3.3.4,<4", "globus-sdk>=4.8.0,<5"]`;
  `[tool.pixi.pypi-dependencies] ai-almanac = { path = ".", editable =
  true, extras = ["cloud"] }`. Regenerate `pixi.lock` via `pixi install`
  (tool-owned).
- `Dockerfile:25`: `pip wheel --no-cache-dir --wheel-dir /wheels ".[cloud]"`.
- `db.py`/`sync_db.py` (optional hardening): catch
  `ModuleNotFoundError: psycopg` with "install ai-almanac[cloud]".

**Step 7 — compose/scripts cleanup.** Delete `compose.local-gcs.yaml`,
`compose.local-modal.yaml`; remove the two pixi tasks; strip `--storage`
from `scripts/self-host-local.sh`.

**Step 8 — tests** (see Test strategy).

**Step 9 — terraform** (`terraform/modules/almanac-env/main.tf` +
`variables.tf`):
- Add `volumes { name="outputs" gcs { bucket =
  google_storage_bucket.job_outputs.name } }` and a data-bucket volume;
  `volume_mounts` at `/mnt/outputs` and `/mnt/data`. Set gcsfuse
  **implicit-dirs** via `mount_options` if not the Cloud Run default —
  Modal uploads create no directory placeholder objects, so without
  implicit dirs the app cannot see them (hard requirement; Phase-0 spike
  must confirm).
- Env changes: remove `STORAGE_BACKEND`, `GCS_DATA_BUCKET`,
  `GCS_UPLOADS_BUCKET`, `GCS_OUTPUTS_BUCKET`, `CPL_MACHINE_IS_GCE`; add
  `OUTPUT_DIR=/mnt/outputs`, `SHARED_CACHE_DIR=/mnt/data/cache`,
  `DATASET_MOUNT_ROOTS=/mnt/data`, `BUCKET_MOUNTS` JSON. IAM
  (`backend_reads_data` objectViewer, `backend_reads_outputs` objectAdmin)
  already suffices for FUSE; `backend_signs_urls` can be removed.
- Deploy runbook (PR description): redeploy Modal apps first; one-off SQL
  to re-path `data_sources` rows from `gs://bucket/x` → `/mnt/data/x`.

**Step 10 — docs + web copy + API types.** `DEVELOPMENT.md:154`,
`docs/deployment.md`, `terraform/README.md`, `CLAUDE.md` gs:// mentions;
`web/src/routes/data-sources/+page.svelte` copy (lines 317, 347, 351) →
mount-path phrasing; `pixi run generate-api-types` (CI enforces staleness).

**Step 11 — CI.** Add a `bare-wheel` job to `.github/workflows/ci.yml`:
build the frontend, `pip install . pytest pytest-asyncio` in a clean venv
(no extras), run `pytest tests`, assert `python -c "import modal"` fails.
Gate cloud-only tests with `pytest.importorskip("modal")`
(`tests/test_modal_app.py` loads `modal/app.py`, which imports the SDK at
module level; `test_modal_runner.py` needs no gate — it fakes the module).

**Step 12 — `/security-review` before push** (server/ + terraform/ both
touched — mandatory).

## Settings changes

| Field | Change |
|---|---|
| `storage_backend`, `gcs_uploads_bucket`, `gcs_outputs_bucket`, `gcs_data_bucket` | **Removed.** Env/config.yaml/DB-overlay values silently ignored (verified: `extra="ignore"` + `_apply_overlay` key filter); overlay presence logs a deprecation warning for one release. |
| `bucket_mounts: dict[str,str] = {}` | **Added.** Mount path → gs:// URI; Modal-dispatch translation only. `SHARED_ENV_ONLY_FIELDS`. |
| `shared_cache_dir: str = ""` | **Added.** Root for `blend-intermediates/` + `season-forecasts/` prefixes; empty = local `cache_dir()`. `SHARED_ENV_ONLY_FIELDS`. |

## Test strategy

**Delete:** `tests/test_gcs_storage.py`, `tests/test_gcs_source_validation.py`.

**Update:** `tests/test_auth.py` (drop
`test_enforce_shared_gcs_skips_mount_roots` ~line 497 and the sibling at
~526; add the modal⇒outputs-mapping invariant test);
`tests/test_data_sources.py` "Ownership and pointer (gs://) registration"
section (~line 394) → mount-roots rule (containment accepted, outside
rejected, admin exempt); `tests/test_modal_runner.py` → configs become
mount paths + `settings.bucket_mounts` monkeypatch; assert `spawn_args`
carries the *translated* gs:// config and derived bucket while
`_job_config` returned mount paths.

**Unchanged-but-verify:** `test_blend_submission.py`, `test_blend_domain.py`,
`test_chat_blend_endpoints.py`, `test_guardrail_enforcement.py`,
`test_assistant_trust_boundary.py` seed gs:// source paths directly — paths
are opaque to submission, so they pass; optionally normalize fixtures to
`/mnt/...` for hygiene. `test_forecast_pipeline.py::_split_gs_uri` stays
(worker-side code stays).

**Add:** `tests/test_bucket_mounts.py` — longest-prefix matching, gs://
passthrough, nested `blend_config_snapshot` + `model_files` translation,
cache-URI injection/omission, bare-bucket outputs derivation, original
config not mutated. Extend `tests/test_artifacts_publication.py` for
recursive `list_result_files` / nested `result_file_path` containment
(reject `../` escape). Router test:
`GET /jobs/{id}/results/output/{model}/manifest.json` serves nested files
locally.

## Verification commands

```bash
pixi install                     # regenerates pixi.lock (tool-owned)
pixi run check && pixi run test
pixi run generate-api-types && git diff --exit-code web/src/lib/api-types.gen.ts
pixi run package
python -m venv /tmp/bare && /tmp/bare/bin/pip install dist/ai_almanac-*.whl
/tmp/bare/bin/python -c "import ai_almanac.server.app"        # boots without cloud SDKs
/tmp/bare/bin/python -c "import modal" && echo "FAIL: modal leaked into core"
/tmp/bare/bin/pip install 'dist/ai_almanac-*.whl[cloud]'      # extra resolves
grep -rn "gs://" src/ai_almanac --include="*.py" | grep -v forecast_pipeline
# expect only modal_runner/bucket_mounts comments
# staging: submit benchmark + blend + forecast via Modal; confirm
# results/logs/tiles render through the FUSE mount
```

## Open questions

1. **Uploads bucket** — dead code in cloud today (`_uploads_bucket` never
   read; Modal preflight rejects local uploads anyway). Recommend: don't
   mount it, leave the terraform bucket in place, revisit with the
   export/sharing work. Confirm before deleting the terraform resource.
2. **Cloud Run gcsfuse implicit-dirs** — default-on for Cloud Run GCS
   volumes on provider 6.50, or does it need `mount_options`? Must be
   settled by the Phase-0 spike; it's the one thing that can make Modal
   outputs invisible.
3. **Artifact checksums for cloud jobs** switch from GCS MD5 to sha256
   streamed through FUSE (already backend-inconsistent today) — but
   `publish_pending` now streams every output byte through gcsfuse. Watch
   the first staging deploy for indexing latency on big forecast jobs.
4. **Admin registrations in shared mode** — the new router rule exempts
   admins (parity with today's gs:// rule); `dataset_mount_roots` still
   constrains resolution. Tighten to everyone?
5. **`trajectory_sets.storage_prefix`** old rows keep gs:// values
   (bookkeeping/display only) — verified nothing round-trips them into a
   job config today; confirm that stays true.
