"""Modal app for live, on-demand AI weather model forecast generation.

Deploy with:
    modal deploy modal/forecasts_app.py

After deploying, or whenever a model's upstream weights change, warm the
weight cache once so real forecast jobs never pay for the download inline:
    modal run modal/forecasts_app.py::warm_model_weights

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

import importlib.util
import io
import json
import os
import tempfile
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

inference_image = (
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
        "'earth2studio[aifs,data,fuxi,graphcast] @ git+https://github.com/NVIDIA/earth2studio.git@0.14.0'",
        "unset PIP_CONSTRAINT && uv pip install --system --break-system-packages "
        "google-cloud-storage numpy 'xarray<2026.0.0' zarr gcsfs h5netcdf onnxruntime-gpu pyyaml",
    )
    .add_local_file(FORECAST_MODELS_YAML, "/almanac/forecast_models.yaml")
    .add_local_file(FORECAST_PIPELINE_PY, "/almanac/forecast_pipeline.py")
)

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
        "google-cloud-storage",
        "gcsfs",
        "h5netcdf",
        "numpy",
        "rasterio",
        "xarray<2026.0.0",
        "zarr",
        "pyyaml",
    )
    .add_local_file(FORECAST_MODELS_YAML, "/almanac/forecast_models.yaml")
    .add_local_file(FORECAST_PIPELINE_PY, "/almanac/forecast_pipeline.py")
)


def _load_pipeline():
    """Load the bundled forecast_pipeline.py module by explicit path — the
    container has no `ai_almanac` package installed, only this one file.
    """
    spec = importlib.util.spec_from_file_location(
        "forecast_pipeline", "/almanac/forecast_pipeline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
            try:
                stream.flush()
            except Exception:
                pass


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


def _registry_entry(model_id: str) -> dict:
    registry = _load_model_registry()
    for entry in registry.get("models") or []:
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


@app.function(
    image=inference_image,
    gpu="A100-80GB",
    cpu=(8, 16),
    memory=(32768, 65536),
    timeout=7200,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def run_forecast_inference(job_id: str, model_id: str, config: dict) -> dict:
    """Run one AI weather model against the latest GFS conditions; write its
    raw output to scratch volume space for render_forecast_products to read."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")

    pipeline = _load_pipeline()
    model_entry = _registry_entry(model_id)
    zarr_path = _zarr_volume_path(job_id, model_id)
    run_info = pipeline.run_forecast_inference(config, model_entry, zarr_path)
    forecast_volume.commit()
    return run_info


@app.function(
    image=inference_image,
    gpu="A100-80GB",
    cpu=(8, 16),
    memory=(32768, 65536),
    timeout=7200,
    volumes={"/cache": forecast_volume},
)
def warm_model_weights() -> None:
    """Download every registered model's weights into the persistent volume
    once, so run_forecast_inference/run_season_forecast_bundle never pay for
    the download inline inside a real, timed forecast job. Re-run manually
    after adding a model to the registry or when upstream weights change —
    see the module docstring for the `modal run` invocation."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")

    pipeline = _load_pipeline()
    registry = _load_model_registry()
    for entry in registry.get("models") or []:
        model_class = entry["earth2studio_class"]
        print(f"==> Warming weights: {model_class}")
        pipeline.load_model(model_class)
    forecast_volume.commit()
    print("==> Weight cache warmed")


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
    model_entry = _registry_entry(model_id)
    zarr_path = _zarr_volume_path(job_id, model_id)

    with tempfile.TemporaryDirectory(prefix=f"forecast-products-{job_id}-{model_id}-") as tmp:
        root = Path(tmp)
        pipeline.render_forecast_products(
            {**config, "job_id": job_id}, model_id, model_entry, run_info, zarr_path, root
        )
        _upload_output_dir_to_gcs(client, outputs_bucket, job_id, model_id, root)

    _cleanup_volume_scratch(job_id, model_id)


@app.function(
    image=inference_image,
    gpu="A100-80GB",
    cpu=(8, 16),
    memory=(32768, 65536),
    # Many sequential issue-date runs per season, not one — a much longer
    # ceiling than the single-run map-visualization inference function.
    timeout=21600,
    secrets=[gcp_secret],
    volumes={"/cache": forecast_volume},
)
def run_season_forecast_bundle(
    job_id: str, model_id: str, config: dict, season_params: dict
) -> bytes:
    """Loop one model across the current season's issue dates and return a
    tar.gz bundle containing one NetCDF matching the historical `{year}.nc`
    schema, the same bundle shape run_blend's forecast_bundles expect."""
    os.environ.setdefault("EARTH2STUDIO_CACHE", "/cache/earth2studio")
    os.environ.setdefault("XDG_CACHE_HOME", "/cache")

    pipeline = _load_pipeline()
    model_entry = _registry_entry(model_id)
    year = datetime.now(UTC).year
    stage_root = Path(tempfile.mkdtemp(prefix=f"season-{job_id}-{model_id}-"))
    scratch_root = Path(tempfile.mkdtemp(prefix=f"season-scratch-{job_id}-{model_id}-"))
    out_path = stage_root / f"{year}.nc"
    pipeline.generate_season_forecast_netcdf(
        model_entry, config, season_params, scratch_root, out_path
    )
    forecast_volume.commit()
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

    with redirect_stdout(_LogTee(sys.stdout, log_buffer)), redirect_stderr(
        _LogTee(sys.stderr, log_buffer)
    ):
        try:
            _write_gcp_credentials_from_secret()
            client = gcs.Client()
        except Exception:
            traceback.print_exc()

        model_ids = config["forecast_model_ids"]
        failures: dict[str, str] = {}

        print(f"==> Running forecast inference: {model_ids}")
        inference_calls = {
            model_id: run_forecast_inference.spawn(job_id, model_id, config)
            for model_id in model_ids
        }
        run_infos: dict[str, dict] = {}
        for model_id, call in inference_calls.items():
            try:
                run_infos[model_id] = call.get()
            except Exception as exc:
                failures[model_id] = str(exc)
                traceback.print_exc()

        print(f"==> Rendering forecast products: {list(run_infos)}")
        render_calls = {
            model_id: render_forecast_products.spawn(
                job_id, model_id, config, run_info, outputs_bucket
            )
            for model_id, run_info in run_infos.items()
        }
        for model_id, call in render_calls.items():
            try:
                call.get()
                print(f"==> Done: {model_id}")
            except Exception as exc:
                failures[model_id] = str(exc)
                traceback.print_exc()

        blend_config = config.get("blend_config_snapshot")
        season_model_params = config.get("season_model_params") or {}
        if blend_config:
            season_model_ids = [m for m in model_ids if m not in failures]
            print(f"==> Running season-long inference for blend scoring: {season_model_ids}")
            season_calls = {
                model_id: run_season_forecast_bundle.spawn(
                    job_id, model_id, config, season_model_params.get(model_id) or {}
                )
                for model_id in season_model_ids
            }
            live_forecast_bundles: dict[str, bytes] = {}
            for model_id, call in season_calls.items():
                try:
                    live_forecast_bundles[model_id] = call.get()
                except Exception as exc:
                    failures[f"{model_id} (season)"] = str(exc)
                    traceback.print_exc()

            missing = [name for name in model_ids if name not in live_forecast_bundles]
            if not missing:
                try:
                    print("==> Scoring live season against the trained blend")
                    live_year = datetime.now(UTC).year
                    modal.Function.from_name(
                        "almanac-blending", "score_live_forecast_bundle"
                    ).remote(
                        job_id, blend_config, live_forecast_bundles, live_year, outputs_bucket
                    )
                except Exception as exc:
                    failures["blend_scoring"] = str(exc)
                    traceback.print_exc()
            else:
                print(
                    f"==> Skipping blend scoring; missing season data for {missing}"
                )

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
