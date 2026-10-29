"""Modal app for live, on-demand AI weather model forecast generation.

Deploy with:
    modal deploy modal/forecasts_app.py

After deploying, or whenever a model's upstream weights change, warm the
weight cache once per model-group environment so real forecast jobs never pay
for the download inline (each has its own image — see INFERENCE_IMAGES):
    modal run modal/forecasts_app.py::warm_model_weights_base
    modal run modal/forecasts_app.py::warm_model_weights_aifs2
    modal run modal/forecasts_app.py::warm_model_weights_aifs2ens

To try a season end-to-end with this checkout before deploying (see
smoke_season_forecast):
    modal run --env staging modal/forecasts_app.py::smoke_season_forecast --reset

run_forecast executes a forecast job's season plan: each blend member's AI
weather model (from server/config/forecast_models.yaml) is rolled out with
earth2studio across its planned issue dates, and the season is scored against
the trained blend by the almanac-blending app.

Shares the (job_id, config, outputs_bucket) signature with run_benchmark and
run_blend so the platform's ModalRunner dispatches it identically.

This file is intentionally thin: the planning, earth2studio inference, store,
and season assembly logic lives in
`ai_almanac.server.services.forecast_pipeline` (a self-contained module with
no Modal dependency), bundled into the image as a single file and imported
directly. The exact same module also runs inside the local `forecast` pixi
environment (see envs/forecast_entrypoint.py) — this file only adds the
Modal-specific plumbing: GPU/image selection, the CPU/GPU split, the weights
Volume, GCS credentials, and cross-app calls.

Secrets required:
    modal secret create gcp-service-account SERVICE_ACCOUNT_JSON="$(cat key.json)"
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import modal

APP_NAME = "almanac-forecasts"
GCP_SECRET_NAME = "gcp-service-account"
_REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_MODELS_YAML = _REPO_ROOT / "src/ai_almanac/server/config/forecast_models.yaml"
FORECAST_PIPELINE_PY = _REPO_ROOT / "src/ai_almanac/server/services/forecast_pipeline.py"
# Loaded by forecast_pipeline from forecast_model_opt/ beside it (load_model
# applies them).
AIFS_SPEEDUPS_PY = _REPO_ROOT / "src/ai_almanac/server/services/forecast_model_opt/aifs_speedups.py"
ANEMOI_TRITON_GT_PY = (
    _REPO_ROOT / "src/ai_almanac/server/services/forecast_model_opt/anemoi_triton_gt.py"
)

app = modal.App(APP_NAME)

# Two unrelated things share this volume, at different subtrees:
#  - /cache/season-forecasts: the trajectory store for jobs that name no GCS
#    bucket — in practice only smoke tests (see _VOLUME_STORE).
#  - /cache/earth2studio: downloaded model weights (EARTH2STUDIO_CACHE below).
#    A few GB per model, well under Modal's per-volume soft limit, and never
#    cleaned up — see warm_model_weights. Baking weights into the image was
#    the other option, but it forces a full re-download on every deploy that
#    touches an earlier image layer (e.g. forecast_pipeline.py); a volume
#    decouples the weight cache from code changes entirely.
forecast_volume = modal.Volume.from_name("earth2studio-cache", create_if_missing=True)

gcp_secret = modal.Secret.from_name(GCP_SECRET_NAME)

# One inference image per model-group environment — the AIFS families pin
# incompatible anemoi-models versions and can't share one image (see
# forecast.pixi.toml). A model's `env` field in forecast_models.yaml selects
# which image runs it; keep these extras in sync with that manifest.
_INFERENCE_EXTRAS = {
    "base": ["aifs", "aifsens", "aurora", "data", "fuxi", "gencast", "graphcast"],
    "aifs2": ["aifs2", "data"],
    "aifs2ens": ["aifs2ens", "data"],
}


def _inference_image(extras: list[str]) -> modal.Image:
    spec = ",".join(extras)
    return (
        modal.Image.from_registry("nvcr.io/nvidia/pytorch:25.12-py3")
        .env({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"})
        .apt_install(
            "git",
            "make",
            "curl",
            "cmake",
            "python3-dev",
            "libeccodes-tools",
            "libeccodes-dev",
        )
        .run_commands(
            "python -m pip install --upgrade uv",
            "unset PIP_CONSTRAINT && uv pip install --system --break-system-packages "
            # The 0.19.0 release commit. Keep in sync with
            # src/ai_almanac/envs/forecast.pixi.toml.
            f"'earth2studio[{spec}] @ git+https://github.com/NVIDIA/earth2studio.git"
            "@1a6e3669db0b04c1b42253cf0e631ed1ee8817ea'",
            "unset PIP_CONSTRAINT && uv pip install --system --break-system-packages "
            "dask google-cloud-storage numpy 'xarray<2026.0.0' zarr gcsfs 'h5netcdf[h5py]' "
            "onnxruntime-gpu pyyaml",
        )
        .add_local_file(FORECAST_MODELS_YAML, "/almanac/forecast_models.yaml")
        .add_local_file(FORECAST_PIPELINE_PY, "/almanac/forecast_pipeline.py")
        .add_local_file(AIFS_SPEEDUPS_PY, "/almanac/forecast_model_opt/aifs_speedups.py")
        .add_local_file(ANEMOI_TRITON_GT_PY, "/almanac/forecast_model_opt/anemoi_triton_gt.py")
    )


INFERENCE_IMAGES = {env: _inference_image(extras) for env, extras in _INFERENCE_EXTRAS.items()}

# CPU work: orchestration, the store sweep, and season assembly.
cpu_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "dask",
        "google-cloud-storage",
        "gcsfs",
        "h5netcdf[h5py]",
        # Same pin as earth2studio, so season assembly picks the same netCDF
        # engine here as it did in the inference image.
        "netCDF4<1.7.3",
        "numpy",
        "xarray<2026.0.0",
        "zarr",
        "pyyaml",
    )
    .add_local_file(FORECAST_MODELS_YAML, "/almanac/forecast_models.yaml")
    .add_local_file(FORECAST_PIPELINE_PY, "/almanac/forecast_pipeline.py")
)


def _load_pipeline(path: Path = Path("/almanac/forecast_pipeline.py")):
    """Load forecast_pipeline.py by explicit path — the container has no
    `ai_almanac` package installed, only this one file, bundled at `path`.

    The module is registered in sys.modules before it runs: dataclasses resolve
    its string annotations through sys.modules, so an unregistered module fails
    on its first @dataclass. The first load wins; later calls return it whatever
    `path` they pass.
    """
    if (loaded := sys.modules.get("forecast_pipeline")) is not None:
        return loaded
    spec = importlib.util.spec_from_file_location("forecast_pipeline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[spec.name]
        raise
    return module


# ---------------------------------------------------------------------------
# GCS plumbing — mirrors modal/blending_app.py's helpers.
# ---------------------------------------------------------------------------


class _LogTee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            with contextlib.suppress(Exception):
                stream.flush()


def _write_gcp_credentials_from_secret() -> None:
    sa_json = os.environ["SERVICE_ACCOUNT_JSON"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(sa_json)
        sa_key_path = f.name
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa_key_path


def _upload_run_log_to_gcs(client, outputs_bucket: str, job_id: str, text: str) -> None:
    if not text or not outputs_bucket:
        return
    client.bucket(outputs_bucket).blob(f"{job_id}/run.log").upload_from_string(
        text, content_type="text/plain"
    )


def _load_model_registry() -> dict:
    import yaml

    return yaml.safe_load(Path("/almanac/forecast_models.yaml").read_text())


# ---------------------------------------------------------------------------
# Modal functions
# ---------------------------------------------------------------------------


def _warm_model_weights_impl(env_name: str) -> None:
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")

    pipeline = _load_pipeline()
    registry = _load_model_registry()
    for entry in registry.get("models") or []:
        if entry.get("env", "base") != env_name:
            continue
        model_class = entry["earth2studio_class"]
        print(f"==> Warming weights: {model_class}")
        pipeline.load_model(model_class)
    print("==> Committing volume")
    t0 = time.perf_counter()
    forecast_volume.commit()
    print(f"==> Weight cache warmed; volume commit took {time.perf_counter() - t0:.1f}s")


# Per-env weight-warming functions, one per image. Defined in a module-level
# loop rather than a factory: Modal requires @app.function targets in global
# scope (a def inside a module-level for loop qualifies; one inside a helper
# function does not). Warming dispatches by registered name, via `modal run
# ...::warm_model_weights_<env>`, so nothing needs the Python handles.
for _env, _image in INFERENCE_IMAGES.items():

    @app.function(
        name=f"warm_model_weights_{_env}",
        image=_image,
        gpu="A100-80GB",
        cpu=(8, 16),
        memory=(32768, 65536),
        timeout=7200,
        volumes={"/cache": forecast_volume},
    )
    def warm_model_weights(env_name: str = _env) -> None:
        _warm_model_weights_impl(env_name)


# Volume-backed trajectory store, used when a job names no GCS bucket. Deployed
# jobs always name one, so in practice this holds only smoke-test rollouts.
_VOLUME_STORE = Path("/cache/season-forecasts")


def _trajectory_store(config: dict) -> str:
    """Root of the shared trajectory store: the job's gs:// trajectory cache
    when it names one, otherwise this app's Volume."""
    store = str(config.get("trajectory_cache_uri") or "").strip()
    if not store:
        # gcs_cache_bucket fallback: remove next release
        cache_bucket = (config.get("gcs_cache_bucket") or "").strip()
        store = f"gs://{cache_bucket}/season-forecasts" if cache_bucket else ""
    if store.startswith("gs://"):
        _write_gcp_credentials_from_secret()
        return store
    return str(_VOLUME_STORE)


def _season_plan(pipeline, config: dict):
    """The job's frozen season plan (see forecast_pipeline.SeasonPlan): which
    model each member runs, on which issue dates, to which horizon."""
    return pipeline.SeasonPlan.parse(config.get("season_plan"))


# A season's missing issue dates are split into chunks, one GPU container each,
# all spawned at once. Each container pays one model load (~1-2 min) for its
# chunk, so a few dates keeps that small against ~12 min per 45-day date.
DATES_PER_ROLLOUT = 4
# Most GPU containers one rollout function (one image) runs at once, shared by
# every forecast job running concurrently. Chunks beyond this queue until a
# container frees up, so cold seasons submitted together can each wait on the
# other's chunks.
MAX_PARALLEL_ROLLOUTS = 12

# Each season function waits on the ones it calls, so each ceiling must outlast
# them plus its own work, or a long cold season is killed from above.
ROLLOUT_TIMEOUT_S = 6 * 3600  # one chunk on one GPU
# The rollout phase waits on every chunk, including those queued behind the
# cap. Waiting costs a fraction of a CPU, so it gets as many full-length rounds
# as Modal's 24 h ceiling leaves room for: a cold ensemble season is ~45
# one-date chunks per model (~4 rounds of ~1.5 h each).
ROLLOUT_ROUNDS_PER_PHASE = 3
ROLLOUTS_PHASE_TIMEOUT_S = ROLLOUT_ROUNDS_PER_PHASE * ROLLOUT_TIMEOUT_S
ASSEMBLE_TIMEOUT_S = 3600
FORECAST_TIMEOUT_S = ROLLOUTS_PHASE_TIMEOUT_S + ASSEMBLE_TIMEOUT_S + 30 * 60  # + scoring


def _season_rollouts_impl(member: str, config: dict, issue_dates: list[str]) -> None:
    """Roll one member's model out for the given issue dates, writing each
    trajectory to the store. The only season step that needs a GPU."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")
    # Data-source temp downloads go to container disk, not the weights Volume
    # (earth2studio.data.utils.datasource_cache_root reads this variable).
    os.environ.setdefault("EARTH2STUDIO_DATA_CACHE", "/tmp/earth2studio-data")

    pipeline = _load_pipeline()
    plan = _season_plan(pipeline, config)
    run = plan.run(member)
    key = plan.key(run)
    store = _trajectory_store(config)
    # Another job for the same model may have stored some of these dates since
    # the caller swept; one more listing keeps the GPU off work already done.
    _refresh_volume_store(store)
    requested = [datetime.fromisoformat(d).date() for d in issue_dates]
    missing = pipeline.unstored_issue_dates(store, key, requested)
    if len(missing) < len(requested):
        print(
            f"==> [{member}] {len(requested) - len(missing)} date(s) were stored meanwhile; "
            f"rolling out {len(missing)}",
            flush=True,
        )
    scratch_root = Path(tempfile.mkdtemp(prefix=f"season-rollouts-{member}-"))
    try:
        pipeline.roll_out_issue_dates(run, key, missing, scratch_root, store)
    except BaseException:
        # Keep the dates already rolled out on a Volume-backed store, without
        # letting a failed commit replace the rollout's own error. A hard kill
        # (timeout, preemption) skips this; the GCS store is durable per date.
        try:
            _commit_volume(member)
        except Exception:
            traceback.print_exc()
        raise
    _commit_volume(member)


def _commit_volume(member: str) -> None:
    print(f"==> [{member}] Committing volume (model weights, volume-backed store)")
    t0 = time.perf_counter()
    forecast_volume.commit()
    print(f"==> [{member}] Volume commit done in {time.perf_counter() - t0:.1f}s")


# Per-env variants, module-level loop for the same global-scope reason as the
# inference/warm functions above; roll_out_missing_dates spawns these by env.
SEASON_ROLLOUT_FNS = {}
for _env, _image in INFERENCE_IMAGES.items():

    @app.function(
        name=f"run_season_rollouts_{_env}",
        image=_image,
        gpu="A100-80GB",
        cpu=(8, 16),
        memory=(32768, 65536),
        timeout=ROLLOUT_TIMEOUT_S,
        max_containers=MAX_PARALLEL_ROLLOUTS,
        secrets=[gcp_secret],
        volumes={"/cache": forecast_volume},
    )
    def run_season_rollouts(member: str, config: dict, issue_dates: list[str]) -> None:
        _season_rollouts_impl(member, config, issue_dates)

    SEASON_ROLLOUT_FNS[_env] = run_season_rollouts


def _rollout_function(run):
    """The GPU rollout function for the image a run's model needs."""
    try:
        return SEASON_ROLLOUT_FNS[run.model.env]
    except KeyError:
        raise ValueError(
            f"{run.member} runs in environment {run.model.env!r}, which has no rollout "
            f"image; known: {sorted(SEASON_ROLLOUT_FNS)}"
        ) from None


def _refresh_volume_store(store: str) -> None:
    """See the Volume as other containers last committed it, when it holds the
    store: a reused warm container otherwise sweeps a stale view."""
    if not store.startswith("gs://"):
        forecast_volume.reload()


def _warn_if_chunks_outrun_the_phase(plan, chunks) -> None:
    """ROLLOUTS_PHASE_TIMEOUT_S allows ROLLOUT_ROUNDS_PER_PHASE full-length
    rounds of MAX_PARALLEL_ROLLOUTS containers per image. More chunks than that
    still run, but slow ones could outlast the phase; say so up front so a
    timeout is easy to explain."""
    from collections import Counter

    per_image = Counter(plan.run(chunk.member).model.env for chunk in chunks)
    capacity = ROLLOUT_ROUNDS_PER_PHASE * MAX_PARALLEL_ROLLOUTS
    crowded = {env: n for env, n in per_image.items() if n > capacity}
    if crowded:
        print(
            f"==> warning: chunks per image {crowded} exceed {ROLLOUT_ROUNDS_PER_PHASE} "
            f"rounds of {MAX_PARALLEL_ROLLOUTS} containers; slow ones may outlast the "
            "rollout phase",
            flush=True,
        )


@app.function(
    image=cpu_image,
    cpu=(0.25, 1),
    memory=1024,
    timeout=ROLLOUTS_PHASE_TIMEOUT_S,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def roll_out_missing_dates(config: dict) -> dict[str, str]:
    """Roll out every issue date the job's plan needs and the store lacks,
    spread over parallel GPU containers. Returns {member: error} for members
    whose store key had a chunk fail; every other member's season is stored.

    Runs on CPU: one store sweep per key, then chunk_rollouts decides the GPU
    containers. A fully stored season spawns none.
    """
    pipeline = _load_pipeline()
    plan = _season_plan(pipeline, config)
    store = _trajectory_store(config)
    _refresh_volume_store(store)

    missing_by_key = {}
    for key, issue_dates in plan.dates_by_key().items():
        missing = pipeline.unstored_issue_dates(store, key, list(issue_dates))
        print(f"==> [{key.model_id}] {len(issue_dates) - len(missing)}/{len(issue_dates)} stored")
        if missing:
            missing_by_key[key] = missing
    chunks = pipeline.chunk_rollouts(plan, missing_by_key, DATES_PER_ROLLOUT)
    print(f"==> Rolling out on {len(chunks)} GPU container(s)", flush=True)
    _warn_if_chunks_outrun_the_phase(plan, chunks)

    # Resolve every chunk's image first, so a bad env spawns nothing.
    targets = [(chunk, _rollout_function(plan.run(chunk.member))) for chunk in chunks]
    pending = []
    failed_keys: dict = {}
    try:
        for chunk, rollout in targets:
            dates = [d.isoformat() for d in chunk.issue_dates]
            pending.append((chunk, rollout.spawn(chunk.member, config, dates)))
        while pending:
            chunk, call = pending[0]
            try:
                call.get()
            except Exception as exc:
                dates = ", ".join(d.isoformat() for d in chunk.issue_dates)
                # The GPU container's own log lives in Modal; name the chunk here.
                print(f"==> [{chunk.member}] rollout chunk failed ({dates}): {exc!r}")
                failed_keys.setdefault(plan.key(plan.run(chunk.member)), f"{exc} ({dates})")
                traceback.print_exc()
            pending.pop(0)
    finally:
        # If this phase dies (an error, an interrupt), stop the GPUs still running.
        for _, call in pending:
            call.cancel()
    _refresh_volume_store(store)
    return {
        run.member: failed_keys[plan.key(run)] for run in plan.runs if plan.key(run) in failed_keys
    }


@app.function(
    image=cpu_image,
    cpu=(1, 2),
    # Assembly streams one slice at a time, but a season with no bounds reads
    # global native-grid trajectories (~190 MB each at 0.25 deg).
    memory=(4096, 32768),
    timeout=ASSEMBLE_TIMEOUT_S,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def assemble_season_bundle(job_id: str, member: str, config: dict) -> bytes:
    """One member's season, from the store, as a tar.gz bundle holding one
    NetCDF in the historical `{year}.nc` schema — the bundle shape run_blend's
    forecast_bundles expect. Runs after roll_out_missing_dates, on CPU."""
    pipeline = _load_pipeline()
    plan = _season_plan(pipeline, config)
    run = plan.run(member)
    key = plan.key(run)
    store = _trajectory_store(config)
    _refresh_volume_store(store)
    missing = pipeline.unstored_issue_dates(store, key, list(run.issue_dates))
    if missing:
        raise RuntimeError(
            f"{member} has no stored trajectory for {[d.isoformat() for d in missing]}"
        )

    stage_root = Path(tempfile.mkdtemp(prefix=f"season-{job_id}-{member}-"))
    out_path = pipeline.assemble_season_netcdf(
        store, key, run, stage_root / "slices", stage_root / f"{plan.season}.nc"
    )
    return pipeline.bundle_files([out_path])


def _season_bundles(job_id: str, plan, config: dict, failures: dict[str, str]) -> dict[str, bytes]:
    """Roll out what the store lacks, then assemble every member in parallel.
    Records each failure in `failures`; any failed rollout skips assembly."""
    try:
        rollout_failures = roll_out_missing_dates.remote(config)
    except Exception as exc:
        failures["rollouts"] = str(exc)
        traceback.print_exc()
        return {}
    failures.update({f"{member} (season)": error for member, error in rollout_failures.items()})
    if rollout_failures:
        # Scoring needs every member, so assembling the rest would be wasted.
        return {}

    calls = {
        run.member: assemble_season_bundle.spawn(job_id, run.member, config) for run in plan.runs
    }
    bundles = {}
    for member, call in calls.items():
        try:
            bundles[member] = call.get()
        except Exception as exc:
            failures[f"{member} (season)"] = str(exc)
            traceback.print_exc()
    return bundles


@app.function(
    image=cpu_image,
    cpu=(0.25, 1),
    memory=1024,
    timeout=FORECAST_TIMEOUT_S,
    secrets=[gcp_secret],
)
def run_forecast(job_id: str, config: dict, outputs_bucket: str) -> None:
    """Build every blend member's season, score it against the trained blend,
    and publish a run.log.

    Two phases: roll out the store's missing issue dates across parallel GPU
    containers (roll_out_missing_dates), then assemble each member's season on
    CPU in parallel (assemble_season_bundle).

    Shares the (job_id, config, outputs_bucket) signature with run_benchmark
    and run_blend so the platform's ModalRunner dispatches it the same way.
    """
    from contextlib import redirect_stderr, redirect_stdout

    from google.cloud import storage as gcs

    log_buffer = io.StringIO()
    client = None

    with (
        redirect_stdout(_LogTee(sys.stdout, log_buffer)),
        redirect_stderr(_LogTee(sys.stderr, log_buffer)),
    ):
        try:
            _write_gcp_credentials_from_secret()
            client = gcs.Client()
        except Exception:
            traceback.print_exc()

        failures: dict[str, str] = {}
        plan = None
        try:
            plan = _season_plan(_load_pipeline(), config)
        except ValueError as exc:
            failures["season_plan"] = str(exc)
            traceback.print_exc()

        # The raw short-lead map deliverable was dropped (D1): its rollout is
        # already contained in the season loop's latest issue date.
        blend_config = config.get("blend_config_snapshot")
        if blend_config and plan is not None:
            members = [run.member for run in plan.runs]
            print(f"==> Running season-long inference for blend scoring: {members}")
            live_forecast_bundles = _season_bundles(job_id, plan, config, failures)

            missing = [member for member in members if member not in live_forecast_bundles]
            if not missing:
                try:
                    print("==> Scoring live season against the trained blend")
                    modal.Function.from_name(
                        "almanac-blending", "score_live_forecast_bundle"
                    ).remote(
                        job_id,
                        {
                            **blend_config,
                            "cache_uri": blend_config.get("cache_uri"),
                            "gcs_cache_bucket": config.get(
                                "gcs_cache_bucket"
                            ),  # fallback: remove next release
                        },
                        live_forecast_bundles,
                        plan.season,
                        outputs_bucket,
                    )
                except Exception as exc:
                    failures["blend_scoring"] = str(exc)
                    traceback.print_exc()
            else:
                print(f"==> Skipping blend scoring; missing season data for {missing}")

        if client is not None:
            try:
                _upload_run_log_to_gcs(client, outputs_bucket, job_id, log_buffer.getvalue())
            except Exception:
                traceback.print_exc()

    if failures:
        raise RuntimeError(
            f"Forecast job {job_id} failed for model(s) {sorted(failures)}; "
            f"see run.log for details: {failures}"
        )


# ---------------------------------------------------------------------------
# Smoke test: run the season pipeline from a laptop before deploying.
# ---------------------------------------------------------------------------

# The local entrypoint needs nothing beyond the modal client, so it runs from a
# standalone `modal` install; registry checks happen inside the containers.

# Ethiopia's bbox from server/config/regions.yaml (lat_min,lat_max,lon_min,
# lon_max) — small enough that the returned bundle stays a few MB.
_SMOKE_BOUNDS = "3,15,33,48"


@app.function(image=cpu_image, timeout=600, volumes={"/cache": forecast_volume})
def reset_volume_store(model_id: str, init_source: str) -> None:
    """Delete one model's volume-backed trajectories so the next smoke run
    starts cold. Never touches the GCS store deployed jobs use."""
    import shutil

    registry_ids = {entry["id"] for entry in _load_model_registry().get("models") or []}
    if model_id not in registry_ids:
        raise ValueError(f"Unknown model {model_id!r}; choose one of {sorted(registry_ids)}")
    init_sources = _load_pipeline().INIT_SOURCES
    if init_source not in init_sources:
        raise ValueError(
            f"Unknown init source {init_source!r}; choose one of {sorted(init_sources)}"
        )
    forecast_volume.reload()
    shutil.rmtree(_VOLUME_STORE / model_id / init_source, ignore_errors=True)
    forecast_volume.commit()


@app.function(image=cpu_image, timeout=600)
def plan_smoke_season(
    model_id: str,
    archive_metadata: dict,
    season_start: str,
    init_source: str,
    max_issue_dates: int,
    lead_days: int,
) -> dict:
    """A one-member season plan built the way submission builds one, from the
    bundled registry and archive metadata given on the command line."""
    pipeline = _load_pipeline()
    try:
        plan = _smoke_season_plan(
            pipeline,
            model_id,
            archive_metadata,
            season_start,
            init_source,
            max_issue_dates,
            lead_days,
        )
    except pipeline.PlanError as exc:
        # PlanError lives in the path-loaded pipeline module, which the local
        # client can't import to unpickle; a builtin keeps the message intact.
        raise ValueError(str(exc)) from None
    return plan.to_dict()


def _smoke_season_plan(
    pipeline, model_id, archive_metadata, season_start, init_source, max_issue_dates, lead_days
):
    entry = next(
        (e for e in _load_model_registry().get("models") or [] if e["id"] == model_id), None
    )
    if entry is None:
        raise pipeline.PlanError(f"Unknown forecast model id: {model_id!r}")
    today = datetime.now(UTC).date()
    return pipeline.plan_season(
        {
            model_id: (
                pipeline.ForecastModel.parse(entry),
                pipeline.ArchiveLayout.parse(archive_metadata),
            )
        },
        season=today.year,
        season_start_month_day=season_start,
        init_source=init_source,
        today=today,
        max_issue_dates=max_issue_dates,
        lead_day=lead_days,
    )


@app.function(image=cpu_image, cpu=1, memory=4096, timeout=600)
def describe_season_bundle(bundle: bytes) -> dict:
    """Shape and value range of a season bundle's NetCDF, for eyeballing."""
    import tarfile

    import xarray as xr

    with tempfile.TemporaryDirectory(prefix="season-bundle-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")
        (path,) = Path(tmp).glob("*.nc")
        with xr.open_dataset(path) as ds:
            tp = ds["tp"].load()
    return {
        "file": path.name,
        "dims": dict(tp.sizes),
        "issue_dates": [str(t)[:10] for t in tp["time"].values],
        "min": float(tp.min()),
        "max": float(tp.max()),
        "nan_fraction": float(tp.isnull().mean()),
    }


def _parse_bounds(bounds: str) -> dict:
    keys = ("lat_min", "lat_max", "lon_min", "lon_max")
    values = [float(part) for part in bounds.split(",")]
    if len(values) != len(keys):
        raise SystemExit(f"--bounds needs {','.join(keys)}; got {bounds!r}")
    return dict(zip(keys, values, strict=True))


@app.local_entrypoint()
def smoke_season_forecast(
    model: str = "fuxi",
    bounds: str = _SMOKE_BOUNDS,
    max_issue_dates: int = 2,
    lead_days: int = 2,
    init_source: str = "gfs",
    init_days: str = "0,3",
    season_start: str = "05-01",
    gcs_bucket: str = "",
    reset: bool = False,
) -> None:
    """Run one model's season through the store sweep, GPU rollouts, and CPU
    assembly with this checkout's code (an ephemeral app, no deploy needed).

    Without --gcs-bucket the trajectories go to the Volume store, so the shared
    GCS store is untouched. Run it twice: the second run should report every
    issue date as stored and start no GPU container. --reset clears
    the Volume store for this model first, to exercise the cold path again;
    it cannot clear the GCS store, so it is refused with --gcs-bucket.

    --lead-days keeps rollouts short (2 days is 8 steps for a 6-hourly model).
    Short rollouts are stored under their own `lead{N}d` key, so they are never
    served as full-length trajectories; --lead-days 45 runs the real horizon.

        modal run --env staging modal/forecasts_app.py::smoke_season_forecast \\
            --model fuxi --max-issue-dates 2 --lead-days 2 --reset
    """
    if reset and gcs_bucket:
        raise SystemExit("--reset clears only the Volume store; drop --gcs-bucket for a cold run")
    archive_metadata = {"init_days": init_days, "spatial_bounds": _parse_bounds(bounds)}
    plan = plan_smoke_season.remote(
        model, archive_metadata, season_start, init_source, max_issue_dates, lead_days
    )
    print(f"==> Planned issue dates: {plan['runs'][0]['issue_dates']}")
    if reset:
        print(f"==> Clearing the Volume store for {model}/{init_source}")
        reset_volume_store.remote(model, init_source)

    config = {"season_plan": plan, "gcs_cache_bucket": gcs_bucket}
    t0 = time.perf_counter()
    rollout_failures = roll_out_missing_dates.remote(config)
    print(f"==> Rollouts done in {time.perf_counter() - t0:.0f}s")
    if rollout_failures:
        raise SystemExit(f"Rollouts failed: {rollout_failures}")
    bundle = assemble_season_bundle.remote(f"smoke-{int(time.time())}", model, config)
    print(f"==> Season bundle ({len(bundle) / 1e6:.1f} MB) in {time.perf_counter() - t0:.0f}s")
    for key, value in describe_season_bundle.remote(bundle).items():
        print(f"    {key}: {value}")
