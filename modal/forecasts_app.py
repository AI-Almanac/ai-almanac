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

Runs one or more AI weather models (from server/config/forecast_models.yaml)
against the latest GFS initial conditions via earth2studio, renders each
variable/lead-hour combination as a Cloud-Optimized GeoTIFF, and publishes
them to gs://{outputs_bucket}/{job_id}/output/{model_id}/ — the same output
contract run_benchmark/run_blend use, so job_artifacts indexes them the same
way.

Shares the (job_id, config, outputs_bucket) signature with run_benchmark and
run_blend so the platform's ModalRunner dispatches it identically.

This file is intentionally thin: the actual earth2studio inference, season
looping, and COG rendering logic lives in
`ai_almanac.server.services.forecast_pipeline` (a self-contained module with
no Modal dependency), bundled into the image as a single file and imported
directly. The exact same module also runs inside the local `forecast` pixi
environment (see envs/forecast_entrypoint.py) — this file only adds the
Modal-specific plumbing: GPU/image selection, Volume scratch space for the
GPU->CPU handoff, GCS staging/upload, secrets, and cross-app calls.

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
from datetime import UTC, datetime
from pathlib import Path

import modal

APP_NAME = "almanac-forecasts"
GCP_SECRET_NAME = "gcp-service-account"
_REPO_ROOT = Path(__file__).resolve().parents[1]
FORECAST_MODELS_YAML = _REPO_ROOT / "src/ai_almanac/server/config/forecast_models.yaml"
FORECAST_PIPELINE_PY = _REPO_ROOT / "src/ai_almanac/server/services/forecast_pipeline.py"

app = modal.App(APP_NAME)

# Two unrelated things share this volume, at different subtrees:
#  - /cache/runs/{job_id}/{model_id}: scratch space for passing the raw
#    earth2studio zarr output between the GPU inference function and the CPU
#    render function (different containers). Cleaned up per job+model once
#    rendering finishes — see _cleanup_volume_scratch.
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
    "base": ["aifs", "aifsens", "data", "fuxi", "gencast", "graphcast"],
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
            # Main SHA, not a release: 0.17.0 lacks the AIFS2/AIFS2ENS time-forcing
            # fix (earth2studio PR #1009). Re-pin to 0.18.0 once tagged. Keep in
            # sync with src/ai_almanac/envs/forecast.pixi.toml.
            f"'earth2studio[{spec}] @ git+https://github.com/NVIDIA/earth2studio.git"
            "@96a9bd607013783da8560fbb7a0bb13a00018b66'",
            "unset PIP_CONSTRAINT && uv pip install --system --break-system-packages "
            "google-cloud-storage numpy 'xarray<2026.0.0' zarr gcsfs h5netcdf onnxruntime-gpu pyyaml",
        )
        .add_local_file(FORECAST_MODELS_YAML, "/almanac/forecast_models.yaml")
        .add_local_file(FORECAST_PIPELINE_PY, "/almanac/forecast_pipeline.py")
    )


INFERENCE_IMAGES = {env: _inference_image(extras) for env, extras in _INFERENCE_EXTRAS.items()}

render_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install(
        "libexpat1",
        "libgdal-dev",
        "libgeos-dev",
        "libproj-dev",
        "proj-data",
        "proj-bin",
    )
    .pip_install(
        "dask",
        "google-cloud-storage",
        "gcsfs",
        "h5netcdf",
        # Same pin as earth2studio, so season assembly picks the same netCDF
        # engine here as it did in the inference image.
        "netCDF4<1.7.3",
        "numpy",
        "rasterio",
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
    on its first @dataclass.
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


def _upload_output_dir_to_gcs(
    client, outputs_bucket: str, job_id: str, model_id: str, local_dir: Path
) -> None:
    from google.cloud.storage import transfer_manager

    if not outputs_bucket:
        raise ValueError("outputs_bucket is required for Modal production runs")
    files = [str(p.relative_to(local_dir)) for p in sorted(local_dir.rglob("*")) if p.is_file()]
    if not files:
        return
    prefix = f"{job_id}/output/{model_id}/"
    print(f"==> Uploading {len(files)} files to gs://{outputs_bucket}/{prefix}")
    bucket = client.bucket(outputs_bucket)
    results = transfer_manager.upload_many_from_filenames(
        bucket,
        files,
        source_directory=str(local_dir),
        blob_name_prefix=prefix,
        worker_type="thread",
    )
    for name, result in zip(files, results, strict=True):
        if isinstance(result, Exception):
            print(f"  upload FAILED: {prefix}{name}: {result}")
        else:
            print(f"  uploaded: {prefix}{name}")


def _upload_run_log_to_gcs(client, outputs_bucket: str, job_id: str, text: str) -> None:
    if not text or not outputs_bucket:
        return
    client.bucket(outputs_bucket).blob(f"{job_id}/run.log").upload_from_string(
        text, content_type="text/plain"
    )


def _load_model_registry() -> dict:
    import yaml

    return yaml.safe_load(Path("/almanac/forecast_models.yaml").read_text())


def _registry_entry(config: dict, member: str) -> dict:
    """Registry entry a blend member runs: the id the server linked it to in
    `forecast_models`. Used only by the map-render functions; season runs
    read their model from the job's season plan."""
    model_id = (config.get("forecast_models") or {}).get(member, member)
    for entry in _load_model_registry().get("models") or []:
        if entry["id"] == model_id:
            return entry
    raise KeyError(f"Unknown forecast model id: {model_id!r}")


_VOLUME_RUNS_ROOT = Path("/cache/runs")


def _zarr_volume_path(job_id: str, model_id: str) -> Path:
    return _VOLUME_RUNS_ROOT / job_id / model_id / "forecast.zarr"


def _cleanup_volume_scratch(job_id: str, model_id: str) -> None:
    import shutil

    shutil.rmtree(_VOLUME_RUNS_ROOT / job_id / model_id, ignore_errors=True)
    forecast_volume.commit()


# ---------------------------------------------------------------------------
# Modal functions
# ---------------------------------------------------------------------------


def _forecast_inference_impl(job_id: str, model_id: str, config: dict) -> dict:
    """Run one AI weather model against the latest GFS conditions; write its
    raw output to scratch volume space for render_forecast_products to read."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")

    pipeline = _load_pipeline()
    model_entry = _registry_entry(config, model_id)
    zarr_path = _zarr_volume_path(job_id, model_id)
    run_info = pipeline.run_forecast_inference(config, model_entry, zarr_path)
    print("==> Committing volume (uploads any newly-downloaded model weights)")
    t0 = time.perf_counter()
    forecast_volume.commit()
    print(f"==> Volume commit done in {time.perf_counter() - t0:.1f}s")
    return run_info


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


# Per-env inference and weight-warming functions, one pair per image. Defined
# in a module-level loop rather than a factory: Modal requires @app.function
# targets in global scope (a def inside a module-level for loop qualifies; one
# inside a helper function does not). Callers dispatch by registered name —
# the server via Function.from_name, warming via `modal run ...::warm_model_
# weights_<env>` — so nothing needs the Python handles.
for _env, _image in INFERENCE_IMAGES.items():

    @app.function(
        name=f"run_forecast_inference_{_env}",
        image=_image,
        gpu="A100-80GB",
        cpu=(8, 16),
        memory=(32768, 65536),
        timeout=7200,
        secrets=[gcp_secret],
        volumes={"/cache": forecast_volume},
    )
    def run_forecast_inference(job_id: str, model_id: str, config: dict) -> dict:
        return _forecast_inference_impl(job_id, model_id, config)

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


@app.function(
    image=render_image,
    cpu=(4, 8),
    memory=(16384, 32768),
    timeout=3600,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def render_forecast_products(
    job_id: str, model_id: str, config: dict, run_info: dict, outputs_bucket: str
) -> None:
    """Render COGs + manifest.json for one model's forecast and publish them
    to gs://{outputs_bucket}/{job_id}/output/{model_id}/, then free the
    scratch volume space run_forecast_inference wrote."""
    from google.cloud import storage as gcs

    _write_gcp_credentials_from_secret()
    client = gcs.Client()
    pipeline = _load_pipeline()
    model_entry = _registry_entry(config, model_id)
    zarr_path = _zarr_volume_path(job_id, model_id)

    with tempfile.TemporaryDirectory(prefix=f"forecast-products-{job_id}-{model_id}-") as tmp:
        root = Path(tmp)
        pipeline.render_forecast_products(
            {**config, "job_id": job_id}, model_id, model_entry, run_info, zarr_path, root
        )
        _upload_output_dir_to_gcs(client, outputs_bucket, job_id, model_id, root)

    _cleanup_volume_scratch(job_id, model_id)


# Volume-backed trajectory store, used when a job names no GCS bucket. Deployed
# jobs always name one, so in practice this holds only smoke-test rollouts.
_VOLUME_STORE = Path("/cache/season-forecasts")


def _trajectory_store(config: dict) -> str:
    """Root of the shared trajectory store: the GCS data bucket when the job
    names one, otherwise this app's Volume."""
    cache_bucket = (config.get("gcs_cache_bucket") or "").strip()
    if cache_bucket:
        _write_gcp_credentials_from_secret()
        return f"gs://{cache_bucket}/season-forecasts"
    return str(_VOLUME_STORE)


def _season_plan(pipeline, config: dict):
    """The job's frozen season plan (see forecast_pipeline.SeasonPlan): which
    model each member runs, on which issue dates, to which horizon."""
    return pipeline.SeasonPlan.parse(config.get("season_plan"))


def _season_rollouts_impl(member: str, config: dict, issue_dates: list[str]) -> None:
    """Roll one member's model out for the given issue dates, writing each
    trajectory to the store. The only season step that needs a GPU."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")
    # Data-source temp downloads go to container disk, not the weights Volume.
    os.environ.setdefault("EARTH2STUDIO_DATA_CACHE", "/tmp/earth2studio-data")

    pipeline = _load_pipeline()
    plan = _season_plan(pipeline, config)
    run = plan.run(member)
    scratch_root = Path(tempfile.mkdtemp(prefix=f"season-rollouts-{member}-"))
    pipeline.roll_out_issue_dates(
        run,
        plan.key(run),
        [datetime.fromisoformat(d).date() for d in issue_dates],
        scratch_root,
        _trajectory_store(config),
    )
    print(f"==> [{member}] Committing volume (model weights, volume-backed store)")
    t0 = time.perf_counter()
    forecast_volume.commit()
    print(f"==> [{member}] Volume commit done in {time.perf_counter() - t0:.1f}s")


# Per-env variants, module-level loop for the same global-scope reason as the
# inference/warm functions above; run_season_forecast_bundle calls these by env.
SEASON_ROLLOUT_FNS = {}
for _env, _image in INFERENCE_IMAGES.items():

    @app.function(
        name=f"run_season_rollouts_{_env}",
        image=_image,
        gpu="A100-80GB",
        cpu=(8, 16),
        memory=(32768, 65536),
        # Many sequential issue-date runs per season, not one — a much longer
        # ceiling than the single-run map-visualization inference function.
        timeout=21600,
        secrets=[gcp_secret],
        volumes={"/cache": forecast_volume},
    )
    def run_season_rollouts(member: str, config: dict, issue_dates: list[str]) -> None:
        _season_rollouts_impl(member, config, issue_dates)

    SEASON_ROLLOUT_FNS[_env] = run_season_rollouts


@app.function(
    image=render_image,
    cpu=(1, 2),
    memory=(4096, 16384),
    # Waits on the GPU rollouts, so it needs at least their ceiling.
    timeout=21600,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def run_season_forecast_bundle(job_id: str, member: str, config: dict) -> bytes:
    """One member's season as a tar.gz bundle holding one NetCDF in the
    historical `{year}.nc` schema — the bundle shape run_blend's
    forecast_bundles expect.

    Runs on CPU: sweeps the trajectory store once, starts a GPU container only
    for the planned issue dates it is missing, then assembles the season from
    the store. A fully stored season never touches a GPU.
    """
    pipeline = _load_pipeline()
    plan = _season_plan(pipeline, config)
    run = plan.run(member)
    key = plan.key(run)
    store = _trajectory_store(config)
    missing = pipeline.unstored_issue_dates(store, key, list(run.issue_dates))
    print(
        f"==> [{member}] {len(run.issue_dates) - len(missing)}/{len(run.issue_dates)} "
        "issue date(s) already stored",
        flush=True,
    )
    if missing:
        SEASON_ROLLOUT_FNS[run.model.env].remote(member, config, [d.isoformat() for d in missing])
        # Pick up trajectories the GPU container committed to a volume-backed store.
        forecast_volume.reload()

    stage_root = Path(tempfile.mkdtemp(prefix=f"season-{job_id}-{member}-"))
    out_path = pipeline.assemble_season_netcdf(
        store, key, run, stage_root / "slices", stage_root / f"{plan.season}.nc"
    )
    return pipeline.bundle_files([out_path])


@app.function(
    image=render_image,
    cpu=(0.25, 1),
    memory=1024,
    timeout=10800,
    secrets=[gcp_secret],
)
def run_forecast(job_id: str, config: dict, outputs_bucket: str) -> None:
    """Run every requested model's inference + rendering concurrently and
    publish a run.log alongside the per-model output directories.

    Models are independent of each other, so each phase fans out with
    .spawn()/.get() instead of awaiting one model's .remote() call before
    starting the next — otherwise this function's own timeout is spent
    waiting on models one at a time instead of in parallel.

    Shares the (job_id, config, outputs_bucket) signature with run_benchmark
    and run_blend so the platform's ModalRunner dispatches it the same way.
    """
    import sys
    import traceback
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
            season_calls = {
                member: run_season_forecast_bundle.spawn(job_id, member, config)
                for member in members
            }
            live_forecast_bundles: dict[str, bytes] = {}
            for member, call in season_calls.items():
                try:
                    live_forecast_bundles[member] = call.get()
                except Exception as exc:
                    failures[f"{member} (season)"] = str(exc)
                    traceback.print_exc()

            missing = [member for member in members if member not in live_forecast_bundles]
            if not missing:
                try:
                    print("==> Scoring live season against the trained blend")
                    live_year = plan.season
                    modal.Function.from_name(
                        "almanac-blending", "score_live_forecast_bundle"
                    ).remote(
                        job_id,
                        {**blend_config, "gcs_cache_bucket": config.get("gcs_cache_bucket")},
                        live_forecast_bundles,
                        live_year,
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


@app.function(image=render_image, timeout=600, volumes={"/cache": forecast_volume})
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
    shutil.rmtree(_VOLUME_STORE / model_id / init_source, ignore_errors=True)
    forecast_volume.commit()


@app.function(image=render_image, timeout=600)
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
    entry = next(
        (e for e in _load_model_registry().get("models") or [] if e["id"] == model_id), None
    )
    if entry is None:
        raise pipeline.PlanError(f"Unknown forecast model id: {model_id!r}")
    today = datetime.now(UTC).date()
    plan = pipeline.plan_season(
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
    return plan.to_dict()


@app.function(image=render_image, cpu=1, memory=4096, timeout=600)
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
    issue date as stored and never start run_season_rollouts_*. --reset clears
    the Volume store for this model first, to exercise the cold path again.

    --lead-days keeps rollouts short (2 days is 8 steps for a 6-hourly model).
    Short rollouts are stored under their own `lead{N}d` key, so they are never
    served as full-length trajectories; --lead-days 45 runs the real horizon.

        modal run --env staging modal/forecasts_app.py::smoke_season_forecast \\
            --model fuxi --max-issue-dates 2 --lead-days 2 --reset
    """
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
    bundle = run_season_forecast_bundle.remote(f"smoke-{int(time.time())}", model, config)
    print(f"==> Season bundle ({len(bundle) / 1e6:.1f} MB) in {time.perf_counter() - t0:.0f}s")
    for key, value in describe_season_bundle.remote(bundle).items():
        print(f"    {key}: {value}")
